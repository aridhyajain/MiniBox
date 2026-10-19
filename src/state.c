/*
 * state.c - container state on disk.
 *
 * OS concept: separate processes share nothing except the kernel and the
 * filesystem. `minibox ps`, `stop` and `rm` are different processes from the
 * one that started the container, so they find it through files:
 *
 *     <state_root>/.lock            flock() serialises "create a container"
 *     <state_root>/<id>/state       key=value lines (see struct container_state)
 *     <state_root>/<id>/log         stdout+stderr of a detached container
 *
 * A file that is rewritten while others read it must never be seen half
 * written, so state_save() writes a temporary file and rename()s it over the
 * old one: rename is atomic, readers see the old or the new file, never a mix.
 */
#define _GNU_SOURCE
#include "state.h"
#include "container.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <net/if.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define STATE_FILE_MAX  16384      /* upper bound for one state file */
#define ID_TRIES        20

/* ------------------------------ paths ------------------------------ */

const char *state_root(void)
{
    const char *env = getenv(STATE_ENV_VAR);

    return (env != NULL && env[0] != '\0') ? env : STATE_DEFAULT_ROOT;
}

int state_path(const char *id, const char *file, char *buf, size_t size)
{
    int n = snprintf(buf, size, "%s/%s/%s", state_root(), id, file);

    return (n < 0 || (size_t)n >= size) ? -1 : 0;
}

/* Directory <root>/<id> (without a trailing file name). */
static int dir_path(const char *id, char *buf, size_t size)
{
    int n = snprintf(buf, size, "%s/%s", state_root(), id);

    return (n < 0 || (size_t)n >= size) ? -1 : 0;
}

/* mkdir -p: create every missing component with `mode`. */
static int mkdir_p(const char *path, mode_t mode)
{
    char buf[PATH_MAX];
    size_t i;

    if (snprintf(buf, sizeof(buf), "%s", path) >= (int)sizeof(buf)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    for (i = 1; buf[i] != '\0'; i++) {
        if (buf[i] != '/')
            continue;
        buf[i] = '\0';
        if (mkdir(buf, mode) == -1 && errno != EEXIST)
            return -1;
        buf[i] = '/';
    }
    if (mkdir(buf, mode) == -1 && errno != EEXIST)
        return -1;
    return 0;
}

/* ------------------------- writing the state file ------------------------- */

/* Values may contain anything; escape \ and line breaks so one line = one value. */
static void put_escaped(FILE *f, const char *s)
{
    for (; *s != '\0'; s++) {
        if (*s == '\\')
            fputs("\\\\", f);
        else if (*s == '\n')
            fputs("\\n", f);
        else if (*s == '\r')
            fputs("\\r", f);
        else
            fputc(*s, f);
    }
}

static void put_string(FILE *f, const char *key, const char *value)
{
    fprintf(f, "%s=", key);
    put_escaped(f, value);
    fputc('\n', f);
}

static void write_fields(FILE *f, const struct container_state *st)
{
    put_string(f, "id", st->id);
    put_string(f, "name", st->name);
    put_string(f, "status", st->status);
    fprintf(f, "pid=%ld\nmonitor_pid=%ld\nexit_code=%d\n", st->pid, st->monitor_pid,
            st->exit_code);
    fprintf(f, "created_at=%lld\nstarted_at=%lld\nfinished_at=%lld\n", st->created_at,
            st->started_at, st->finished_at);
    put_string(f, "command", st->command);
    put_string(f, "rootfs", st->rootfs);
    put_string(f, "hostname", st->hostname);
    put_string(f, "net", st->net);
    fprintf(f, "memory=%lld\ncpus=%g\npids=%ld\ninit=%d\n", st->memory, st->cpus, st->pids,
            st->init);
    put_string(f, "ip", st->ip);
}

/* Flush to the kernel and to disk, then close; any failure is reported. */
static int finish_file(FILE *f, const char *path)
{
    int ok = fflush(f) == 0 && !ferror(f) && fsync(fileno(f)) == 0;

    if (fclose(f) != 0)
        ok = 0;
    if (!ok)
        fprintf(stderr, "minibox: cannot write %s: %s\n", path, strerror(errno));
    return ok ? 0 : -1;
}

int state_save(const struct container_state *st)
{
    char tmp[PATH_MAX], final[PATH_MAX];
    FILE *f;
    int fd;

    if (state_path(st->id, "state.tmp", tmp, sizeof(tmp)) == -1 ||
        state_path(st->id, "state", final, sizeof(final)) == -1) {
        fprintf(stderr, "minibox: state path too long\n");
        return -1;
    }
    fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd == -1) {
        fprintf(stderr, "minibox: open %s: %s\n", tmp, strerror(errno));
        return -1;
    }
    f = fdopen(fd, "w");
    if (f == NULL) {
        perror("fdopen");
        close(fd);
        return -1;
    }
    write_fields(f, st);
    if (finish_file(f, tmp) == -1)
        return -1;
    if (rename(tmp, final) == -1) {          /* the atomic switch-over */
        fprintf(stderr, "minibox: rename %s: %s\n", tmp, strerror(errno));
        return -1;
    }
    return 0;
}

/* ------------------------- reading the state file ------------------------- */

static void copy_str(char *dst, size_t size, const char *src)
{
    snprintf(dst, size, "%s", src);
}

/* Undo put_escaped, in place. */
static void unescape(char *s)
{
    char *out = s;

    for (; *s != '\0'; s++) {
        if (*s == '\\' && s[1] == 'n') {
            *out++ = '\n';
            s++;
        } else if (*s == '\\' && s[1] == 'r') {
            *out++ = '\r';
            s++;
        } else if (*s == '\\' && s[1] == '\\') {
            *out++ = '\\';
            s++;
        } else {
            *out++ = *s;
        }
    }
    *out = '\0';
}

/* Returns 1 if `key` was a text field and has been stored. */
static int set_text_field(struct container_state *st, const char *key, const char *val)
{
    if (strcmp(key, "id") == 0)
        copy_str(st->id, sizeof(st->id), val);
    else if (strcmp(key, "name") == 0)
        copy_str(st->name, sizeof(st->name), val);
    else if (strcmp(key, "status") == 0)
        copy_str(st->status, sizeof(st->status), val);
    else if (strcmp(key, "command") == 0)
        copy_str(st->command, sizeof(st->command), val);
    else if (strcmp(key, "rootfs") == 0)
        copy_str(st->rootfs, sizeof(st->rootfs), val);
    else if (strcmp(key, "hostname") == 0)
        copy_str(st->hostname, sizeof(st->hostname), val);
    else if (strcmp(key, "net") == 0)
        copy_str(st->net, sizeof(st->net), val);
    else if (strcmp(key, "ip") == 0)
        copy_str(st->ip, sizeof(st->ip), val);
    else
        return 0;
    return 1;
}

static void set_number_field(struct container_state *st, const char *key, const char *val)
{
    if (strcmp(key, "pid") == 0)
        st->pid = strtol(val, NULL, 10);
    else if (strcmp(key, "monitor_pid") == 0)
        st->monitor_pid = strtol(val, NULL, 10);
    else if (strcmp(key, "exit_code") == 0)
        st->exit_code = (int)strtol(val, NULL, 10);
    else if (strcmp(key, "created_at") == 0)
        st->created_at = strtoll(val, NULL, 10);
    else if (strcmp(key, "started_at") == 0)
        st->started_at = strtoll(val, NULL, 10);
    else if (strcmp(key, "finished_at") == 0)
        st->finished_at = strtoll(val, NULL, 10);
    else if (strcmp(key, "memory") == 0)
        st->memory = strtoll(val, NULL, 10);
    else if (strcmp(key, "cpus") == 0)
        st->cpus = strtod(val, NULL);
    else if (strcmp(key, "pids") == 0)
        st->pids = strtol(val, NULL, 10);
    else if (strcmp(key, "init") == 0)
        st->init = strtol(val, NULL, 10) != 0;      /* absent in older files: stays 0 */
    /* unknown keys are ignored so newer files can be read by older code */
}

static void parse_line(struct container_state *st, char *line)
{
    char *eq = strchr(line, '=');

    if (eq == NULL)
        return;
    *eq = '\0';
    unescape(eq + 1);
    if (!set_text_field(st, line, eq + 1))
        set_number_field(st, line, eq + 1);
}

/* Read a whole (small) file into buf. Returns its length, or -1 with errno set. */
static ssize_t slurp(const char *path, char *buf, size_t size)
{
    size_t used = 0;
    ssize_t n = 0;
    int fd = open(path, O_RDONLY | O_CLOEXEC);

    if (fd == -1)
        return -1;
    while (used < size - 1) {
        n = read(fd, buf + used, size - 1 - used);
        if (n == -1 && errno == EINTR)
            continue;
        if (n <= 0)
            break;
        used += (size_t)n;
    }
    if (n == -1) {
        int saved = errno;

        close(fd);
        errno = saved;
        return -1;
    }
    close(fd);
    buf[used] = '\0';
    return (ssize_t)used;
}

int state_load(const char *id, struct container_state *st)
{
    char path[PATH_MAX], buf[STATE_FILE_MAX];
    char *line, *save = NULL;

    memset(st, 0, sizeof(*st));
    if (state_path(id, "state", path, sizeof(path)) == -1) {
        errno = ENAMETOOLONG;
        return -1;
    }
    if (slurp(path, buf, sizeof(buf)) == -1)
        return -1;
    for (line = strtok_r(buf, "\n", &save); line != NULL; line = strtok_r(NULL, "\n", &save))
        parse_line(st, line);
    if (st->id[0] == '\0') {
        errno = EINVAL;
        return -1;
    }
    return 0;
}

/* ----------------------------- listing / lookup ----------------------------- */

static int compare_created(const void *a, const void *b)
{
    const struct container_state *x = a, *y = b;

    if (x->created_at != y->created_at)
        return x->created_at < y->created_at ? -1 : 1;
    return strcmp(x->id, y->id);
}

/* Append one entry's state to the array if it can be read. 0 / -1 (out of memory). */
static int append_entry(const char *id, struct container_state **arr, size_t *count,
                        size_t *cap)
{
    struct container_state st;

    if (id[0] == '.' || state_load(id, &st) == -1)
        return 0;                           /* not a container directory: skip */
    if (*count == *cap) {
        size_t ncap = *cap ? *cap * 2 : 8;
        struct container_state *grown = realloc(*arr, ncap * sizeof(**arr));

        if (grown == NULL) {
            perror("realloc");
            return -1;
        }
        *arr = grown;
        *cap = ncap;
    }
    (*arr)[(*count)++] = st;
    return 0;
}

int state_list(struct container_state **out, size_t *count)
{
    struct container_state *arr = NULL;
    size_t n = 0, cap = 0;
    struct dirent *e;
    DIR *d = opendir(state_root());

    *out = NULL;
    *count = 0;
    if (d == NULL) {
        if (errno == ENOENT)
            return 0;                       /* nothing has ever been created */
        fprintf(stderr, "minibox: cannot read %s: %s%s\n", state_root(), strerror(errno),
                errno == EACCES ? " (try sudo)" : "");
        return -1;
    }
    while ((e = readdir(d)) != NULL) {
        if (append_entry(e->d_name, &arr, &n, &cap) == -1) {
            closedir(d);
            free(arr);
            return -1;
        }
    }
    closedir(d);
    if (n > 1)
        qsort(arr, n, sizeof(*arr), compare_created);
    *out = arr;
    *count = n;
    return 0;
}

int state_find(const char *key, struct container_state *st)
{
    struct container_state *all;
    size_t n, i;
    int found = -1;

    if (state_list(&all, &n) == -1)
        return -1;
    for (i = 0; i < n && found == -1; i++)      /* exact ID first, then exact name */
        if (strcmp(all[i].id, key) == 0)
            found = (int)i;
    for (i = 0; i < n && found == -1; i++)
        if (all[i].name[0] != '\0' && strcmp(all[i].name, key) == 0)
            found = (int)i;
    if (found == -1)
        fprintf(stderr, "minibox: no such container: %s\n", key);
    else
        *st = all[found];
    free(all);
    return found == -1 ? -1 : 0;
}

int state_remove(const char *id)
{
    char dir[PATH_MAX];
    struct dirent *e;
    DIR *d;
    int ok = 1;

    if (dir_path(id, dir, sizeof(dir)) == -1)
        return -1;
    d = opendir(dir);
    if (d == NULL) {
        fprintf(stderr, "minibox: cannot open %s: %s\n", dir, strerror(errno));
        return -1;
    }
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
            continue;
        if (unlinkat(dirfd(d), e->d_name, 0) == -1 && errno != ENOENT) {
            fprintf(stderr, "minibox: cannot delete %s/%s: %s\n", dir, e->d_name,
                    strerror(errno));
            ok = 0;
        }
    }
    closedir(d);
    if (ok && rmdir(dir) == -1) {
        fprintf(stderr, "minibox: cannot remove %s: %s\n", dir, strerror(errno));
        ok = 0;
    }
    return ok ? 0 : -1;
}

int state_copy_fd(int in_fd, int out_fd)
{
    char buf[4096];
    ssize_t n, off, w;

    for (;;) {
        n = read(in_fd, buf, sizeof(buf));
        if (n == -1 && errno == EINTR)
            continue;
        if (n == -1) {
            perror("read");
            return -1;
        }
        if (n == 0)
            return 0;                       /* end of file (for now) */
        for (off = 0; off < n; off += w) {
            w = write(out_fd, buf + off, (size_t)(n - off));
            if (w == -1 && errno == EINTR) {
                w = 0;
                continue;
            }
            if (w == -1) {
                perror("write");
                return -1;
            }
        }
    }
}

/* ------------------------------ liveness ------------------------------ */

int process_alive(pid_t pid)
{
    char path[64], buf[512];
    const char *p;

    if (pid <= 0)
        return 0;
    if (kill(pid, 0) == -1 && errno == ESRCH)
        return 0;
    /* A dead process nobody has wait()ed for yet is a zombie: kill() still works. */
    snprintf(path, sizeof(path), "/proc/%d/stat", (int)pid);
    if (slurp(path, buf, sizeof(buf)) == -1)
        return 0;
    p = strrchr(buf, ')');                  /* "pid (comm) S ..." - comm may contain spaces */
    if (p == NULL || p[1] != ' ')
        return 1;
    return p[2] != 'Z' && p[2] != 'X';
}

/*
 * /proc/<pid>/status has "NSpid: <pid in host ns> <pid in child ns> ...".
 * The init of a PID namespace is PID 1 in its own (last) namespace.
 */
int state_pid_is_ns_init(pid_t pid)
{
    char path[64], buf[STATE_FILE_MAX];
    const char *line;
    char *end;
    long value = 0;
    int count = 0;

    snprintf(path, sizeof(path), "/proc/%d/status", (int)pid);
    if (slurp(path, buf, sizeof(buf)) == -1)
        return 0;
    line = strstr(buf, "NSpid:");
    if (line == NULL)
        return 0;
    line += strlen("NSpid:");
    for (;;) {
        long v = strtol(line, &end, 10);

        if (end == line)
            break;
        value = v;
        count++;
        line = end;
    }
    return count >= 2 && value == 1;
}

const char *state_effective_status(const struct container_state *st)
{
    if (strcmp(st->status, STATUS_RUNNING) == 0 && !process_alive((pid_t)st->monitor_pid))
        return STATUS_DEAD;
    return st->status;
}

/* ------------------------------ subnets ------------------------------ */

int state_net_index(const struct container_state *st)
{
    int n;

    if (sscanf(st->ip, "10.0.%d.2", &n) != 1 || n < 0 || n > NET_MAX_INDEX)
        return -1;
    return n;
}

/*
 * Does `s` still own its subnet? A running container does. A dead one (monitor
 * gone) only while its host veth interface still exists - otherwise a stale
 * record would block 10.0.0.x forever.
 */
static int subnet_in_use(const struct container_state *s)
{
    char host[NET_IFNAME_LEN], cont[NET_IFNAME_LEN];

    if (strcmp(s->status, STATUS_RUNNING) != 0 || state_net_index(s) < 0)
        return 0;
    if (process_alive((pid_t)s->monitor_pid))
        return 1;
    if (s->pid <= 0)
        return 0;
    net_veth_names((pid_t)s->pid, host, cont);
    return if_nametoindex(host) != 0;
}

/* Lowest subnet index that no live container uses, or -1 if all are taken. */
static int allocate_subnet(const struct container_state *all, size_t n)
{
    char used[NET_MAX_INDEX + 1];
    size_t i;
    int idx;

    memset(used, 0, sizeof(used));
    for (i = 0; i < n; i++)
        if (subnet_in_use(&all[i]))
            used[state_net_index(&all[i])] = 1;
    for (idx = 0; idx <= NET_MAX_INDEX; idx++)
        if (!used[idx])
            return idx;
    fprintf(stderr, "minibox: no free 10.0.n.0/24 subnet left\n");
    return -1;
}

/* ------------------------------ creating ------------------------------ */

int state_valid_name(const char *name)
{
    size_t i, len = strlen(name);
    int all_hex = (len == STATE_ID_LEN);

    if (len == 0 || len > STATE_NAME_MAX)
        return 0;
    for (i = 0; i < len; i++) {
        char c = name[i];
        int alnum = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');

        if (!alnum && (i == 0 || (c != '_' && c != '.' && c != '-')))
            return 0;
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
            all_hex = 0;
    }
    return !all_hex;                        /* a 12-hex name would look like an ID */
}

void state_init_from_config(struct container_state *st,
                            const struct container_config *cfg, const char *name)
{
    size_t used = 0;
    int i;

    memset(st, 0, sizeof(*st));
    if (name != NULL)
        copy_str(st->name, sizeof(st->name), name);
    for (i = 0; cfg->argv[i] != NULL && used < sizeof(st->command) - 1; i++) {
        int n = snprintf(st->command + used, sizeof(st->command) - used, "%s%s",
                         i > 0 ? " " : "", cfg->argv[i]);

        if (n < 0)
            break;
        used += (size_t)n;                  /* may exceed the buffer: then truncated */
    }
    copy_str(st->rootfs, sizeof(st->rootfs), cfg->rootfs);
    copy_str(st->hostname, sizeof(st->hostname), cfg->hostname);
    copy_str(st->net, sizeof(st->net), cfg->net == NET_VETH ? "veth" : "none");
    st->memory = cfg->limits.memory_bytes;
    st->cpus = (double)cfg->limits.cpu_quota_us / CGROUP_CPU_PERIOD;
    st->pids = cfg->limits.pids_max;
    st->init = cfg->init != 0;
}

/* Take the exclusive lock on <root>/.lock (creating the root first). Returns the fd. */
static int lock_root(void)
{
    char path[PATH_MAX];
    int fd;

    if (mkdir_p(state_root(), 0700) == -1) {
        fprintf(stderr, "minibox: cannot create %s: %s\n", state_root(), strerror(errno));
        return -1;
    }
    if (snprintf(path, sizeof(path), "%s/.lock", state_root()) >= (int)sizeof(path)) {
        fprintf(stderr, "minibox: state path too long\n");
        return -1;
    }
    fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (fd == -1) {
        fprintf(stderr, "minibox: open %s: %s\n", path, strerror(errno));
        return -1;
    }
    while (flock(fd, LOCK_EX) == -1) {
        if (errno != EINTR) {
            perror("flock");
            close(fd);
            return -1;
        }
    }
    return fd;
}

static int random_id(char id[STATE_ID_LEN + 1])
{
    static const char hex[] = "0123456789abcdef";
    unsigned char raw[STATE_ID_LEN / 2];
    ssize_t n;
    size_t i;

    do {
        n = getrandom(raw, sizeof(raw), 0);
    } while (n == -1 && errno == EINTR);
    if (n != (ssize_t)sizeof(raw)) {
        perror("getrandom");
        return -1;
    }
    for (i = 0; i < sizeof(raw); i++) {
        id[2 * i] = hex[raw[i] >> 4];
        id[2 * i + 1] = hex[raw[i] & 0x0f];
    }
    id[STATE_ID_LEN] = '\0';
    return 0;
}

/* Pick a random ID and claim it by mkdir(): EEXIST means a collision, so retry. */
static int claim_id(char id[STATE_ID_LEN + 1])
{
    char dir[PATH_MAX];
    int i;

    for (i = 0; i < ID_TRIES; i++) {
        if (random_id(id) == -1 || dir_path(id, dir, sizeof(dir)) == -1)
            return -1;
        if (mkdir(dir, 0700) == 0)
            return 0;
        if (errno != EEXIST) {
            fprintf(stderr, "minibox: mkdir %s: %s\n", dir, strerror(errno));
            return -1;
        }
    }
    fprintf(stderr, "minibox: could not find a free container ID\n");
    return -1;
}

static int check_name_free(const struct container_state *all, size_t n, const char *name)
{
    size_t i;

    if (name[0] == '\0')
        return 0;
    if (!state_valid_name(name)) {
        fprintf(stderr, "minibox: invalid name '%s' (use letters, digits, _ . -; "
                "at most %d characters; not a 12-digit hex ID)\n", name, STATE_NAME_MAX);
        return -1;
    }
    for (i = 0; i < n; i++) {
        if (strcmp(all[i].name, name) == 0) {
            fprintf(stderr, "minibox: the name '%s' is already used by container %s\n",
                    name, all[i].id);
            return -1;
        }
    }
    return 0;
}

/* Everything state_create does while holding the lock. */
static int create_locked(struct container_state *st)
{
    struct container_state *all;
    size_t n;
    int index = -1, ret = -1;

    if (state_list(&all, &n) == -1)
        return -1;
    if (check_name_free(all, n, st->name) == -1)
        goto out;
    if (strcmp(st->net, "veth") == 0) {
        char host[NET_ADDR_LEN];

        index = allocate_subnet(all, n);
        if (index == -1)
            goto out;
        net_subnet_addrs(index, host, st->ip);      /* container address */
    }
    if (claim_id(st->id) == -1)
        goto out;
    copy_str(st->status, sizeof(st->status), STATUS_RUNNING);
    st->created_at = (long long)time(NULL);
    st->monitor_pid = getpid();
    if (state_save(st) == -1) {
        state_remove(st->id);
        goto out;
    }
    ret = 0;
out:
    free(all);
    return ret;
}

int state_create(struct container_state *st)
{
    int lock = lock_root();
    int ret;

    if (lock == -1)
        return -1;
    ret = create_locked(st);
    close(lock);                            /* closing the fd releases the flock */
    return ret;
}
