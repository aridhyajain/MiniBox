/*
 * cgroup.c - resource limits with cgroups v2.
 *
 * OS concept: a control group is a set of processes whose resource use the
 * kernel accounts for and limits. In cgroup v2 there is one tree, mounted at
 * /sys/fs/cgroup; every directory is a cgroup and every file is a knob.
 * Everything here is plain open/read/write/close on those files.
 *
 * Layout used by minibox:
 *   /sys/fs/cgroup                root: enables memory/cpu/pids for children
 *   └── minibox/                  no processes; enables controllers for children
 *       └── box-<host pid>/       one leaf per container: limits + processes
 *
 * Two v2 rules shape this:
 *  1. A controller can only be used by a cgroup if its PARENT lists it in
 *     cgroup.subtree_control, so we enable top-down: root, then minibox/.
 *  2. "No internal processes": a non-root cgroup that enables controllers for
 *     its children may not contain processes itself. So processes only ever
 *     go into the leaves, never into minibox/.
 */
#define _GNU_SOURCE
#include "cgroup.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <time.h>
#include <unistd.h>

static const char *const CONTROLLERS[] = { "memory", "cpu", "pids" };
#define N_CONTROLLERS (sizeof(CONTROLLERS) / sizeof(CONTROLLERS[0]))

#define REMOVE_TRIES     10          /* 10 x 100 ms = 1 s */
#define REMOVE_DELAY_NS  100000000L

/* ------------------------------ file helpers ------------------------------ */

static int join(char *buf, size_t size, const char *dir, const char *file)
{
    int n = snprintf(buf, size, "%s/%s", dir, file);

    if (n < 0 || (size_t)n >= size) {
        fprintf(stderr, "minibox: path too long: %s/%s\n", dir, file);
        return -1;
    }
    return 0;
}

/* Write a string to a cgroup file. The kernel reports bad values from write(). */
static int write_file(const char *path, const char *value)
{
    size_t len = strlen(value);
    ssize_t n;
    int fd = open(path, O_WRONLY | O_CLOEXEC);

    if (fd == -1) {
        fprintf(stderr, "minibox: open %s: %s\n", path, strerror(errno));
        return -1;
    }
    n = write(fd, value, len);
    if (n != (ssize_t)len) {
        fprintf(stderr, "minibox: write '%s' to %s: %s\n", value, path,
                n == -1 ? strerror(errno) : "short write");
        close(fd);
        return -1;
    }
    if (close(fd) == -1) {
        perror(path);
        return -1;
    }
    return 0;
}

/* Read a (small) cgroup file into buf as a NUL-terminated string. */
static int read_file(const char *path, char *buf, size_t size)
{
    size_t used = 0;
    ssize_t n;
    int fd = open(path, O_RDONLY | O_CLOEXEC);

    if (fd == -1) {
        fprintf(stderr, "minibox: open %s: %s\n", path, strerror(errno));
        return -1;
    }
    while (used < size - 1 && (n = read(fd, buf + used, size - 1 - used)) != 0) {
        if (n == -1) {
            if (errno == EINTR)
                continue;
            fprintf(stderr, "minibox: read %s: %s\n", path, strerror(errno));
            close(fd);
            return -1;
        }
        used += (size_t)n;
    }
    buf[used] = '\0';
    close(fd);
    return 0;
}

/* write_file(dir/file, value) */
static int write_in(const char *dir, const char *file, const char *value)
{
    char path[CGROUP_PATH_LEN];

    if (join(path, sizeof(path), dir, file) == -1)
        return -1;
    return write_file(path, value);
}

static int file_exists(const char *dir, const char *file)
{
    char path[CGROUP_PATH_LEN];

    return join(path, sizeof(path), dir, file) == 0 && access(path, F_OK) == 0;
}

/* Is `word` one of the space-separated words in `list`? */
static int has_word(const char *list, const char *word)
{
    size_t len = strlen(word);
    const char *p = list;

    while ((p = strstr(p, word)) != NULL) {
        int starts = (p == list || p[-1] == ' ');
        int ends = (p[len] == '\0' || p[len] == ' ' || p[len] == '\n');

        if (starts && ends)
            return 1;
        p += len;
    }
    return 0;
}

/* --------------------------------- checks --------------------------------- */

int cgroup_limits_set(const struct cgroup_limits *lim)
{
    return lim->memory_bytes > 0 || lim->cpu_quota_us > 0 || lim->pids_max > 0;
}

int cgroup_check_v2(void)
{
    char list[512];
    struct statfs sfs;
    size_t i;

    if (statfs(CGROUP_ROOT, &sfs) == -1) {
        perror("statfs(" CGROUP_ROOT ")");
        return -1;
    }
    /* cgroup v2 is its own filesystem type, identified by its magic number */
    if (sfs.f_type != CGROUP2_SUPER_MAGIC) {
        fprintf(stderr, "minibox: cgroup v2 is not mounted at " CGROUP_ROOT
                " (limits need cgroup v2; run scripts/check_env.sh)\n");
        return -1;
    }
    if (read_file(CGROUP_ROOT "/cgroup.controllers", list, sizeof(list)) == -1)
        return -1;
    for (i = 0; i < N_CONTROLLERS; i++) {
        if (!has_word(list, CONTROLLERS[i])) {
            fprintf(stderr, "minibox: cgroup controller '%s' is not available "
                    "(not in " CGROUP_ROOT "/cgroup.controllers)\n", CONTROLLERS[i]);
            return -1;
        }
    }
    return 0;
}

/* ---------------------------------- setup ---------------------------------- */

/* Enable memory/cpu/pids for the children of `dir` (only the missing ones). */
static int enable_controllers(const char *dir)
{
    char path[CGROUP_PATH_LEN], current[512], value[32];
    size_t i;

    if (join(path, sizeof(path), dir, "cgroup.subtree_control") == -1)
        return -1;
    if (read_file(path, current, sizeof(current)) == -1)
        return -1;
    for (i = 0; i < N_CONTROLLERS; i++) {
        if (has_word(current, CONTROLLERS[i]))
            continue;
        snprintf(value, sizeof(value), "+%s", CONTROLLERS[i]);
        if (write_file(path, value) == -1)
            return -1;
    }
    return 0;
}

/* Top-down: root enables for minibox/, minibox/ enables for the leaves. */
static int prepare_base(void)
{
    if (enable_controllers(CGROUP_ROOT) == -1)
        return -1;
    if (mkdir(CGROUP_BASE, 0755) == -1 && errno != EEXIST) {
        perror("mkdir(" CGROUP_BASE ")");
        return -1;
    }
    return enable_controllers(CGROUP_BASE);
}

/* memory.max = hard limit in bytes; swap 0 so the limit cannot be dodged. */
static int write_memory_limit(const char *leaf, long long bytes)
{
    char value[32];

    snprintf(value, sizeof(value), "%lld", bytes);
    if (write_in(leaf, "memory.max", value) == -1)
        return -1;
    if (file_exists(leaf, "memory.swap.max"))
        return write_in(leaf, "memory.swap.max", "0");
    return 0;
}

/* cpu.max = "<quota> <period>": run at most quota us in every period us. */
static int write_cpu_limit(const char *leaf, long quota_us)
{
    char value[64];

    snprintf(value, sizeof(value), "%ld %d", quota_us, CGROUP_CPU_PERIOD);
    return write_in(leaf, "cpu.max", value);
}

/* pids.max = maximum number of processes/threads; fork() fails beyond it. */
static int write_pids_limit(const char *leaf, long pids)
{
    char value[32];

    snprintf(value, sizeof(value), "%ld", pids);
    return write_in(leaf, "pids.max", value);
}

static int apply_limits(const char *leaf, const struct cgroup_limits *lim)
{
    if (lim->memory_bytes > 0 && write_memory_limit(leaf, lim->memory_bytes) == -1)
        return -1;
    if (lim->cpu_quota_us > 0 && write_cpu_limit(leaf, lim->cpu_quota_us) == -1)
        return -1;
    if (lim->pids_max > 0 && write_pids_limit(leaf, lim->pids_max) == -1)
        return -1;
    /* on OOM, kill every process of the container together, not just one */
    return write_in(leaf, "memory.oom.group", "1");
}

/* Writing a PID to cgroup.procs moves that process into the cgroup. */
static int add_process(const char *leaf, pid_t pid)
{
    char value[32];

    snprintf(value, sizeof(value), "%d", (int)pid);
    return write_in(leaf, "cgroup.procs", value);
}

int cgroup_setup(struct cgroup *cg, pid_t pid, const struct cgroup_limits *lim)
{
    int n;

    cg->created = 0;
    if (prepare_base() == -1)
        return -1;
    n = snprintf(cg->path, sizeof(cg->path), "%s/box-%d", CGROUP_BASE, (int)pid);
    if (n < 0 || (size_t)n >= sizeof(cg->path)) {
        fprintf(stderr, "minibox: cgroup path too long\n");
        return -1;
    }
    if (mkdir(cg->path, 0755) == -1) {
        perror(cg->path);
        return -1;
    }
    cg->created = 1;
    if (apply_limits(cg->path, lim) == -1)
        return -1;
    return add_process(cg->path, pid);
}

/* ---------------------------------- stats ---------------------------------- */

/*
 * Read a statistic from leaf/file. key == NULL: the file holds one number
 * (memory.peak, pids.peak). Otherwise find the "key value" line (cpu.stat,
 * memory.events). Returns 0, or -1 if the file/key is missing (silently).
 */
static int read_stat(const char *leaf, const char *file, const char *key,
                     long long *out)
{
    char path[CGROUP_PATH_LEN], buf[2048], name[64];
    char *line, *save = NULL;
    long long value;

    if (!file_exists(leaf, file) || join(path, sizeof(path), leaf, file) == -1)
        return -1;
    if (read_file(path, buf, sizeof(buf)) == -1)
        return -1;
    if (key == NULL)
        return sscanf(buf, "%lld", out) == 1 ? 0 : -1;
    for (line = strtok_r(buf, "\n", &save); line != NULL;
         line = strtok_r(NULL, "\n", &save)) {
        if (sscanf(line, "%63s %lld", name, &value) == 2 && strcmp(name, key) == 0) {
            *out = value;
            return 0;
        }
    }
    return -1;
}

void cgroup_print_stats(const struct cgroup *cg)
{
    long long v;

    if (read_stat(cg->path, "memory.peak", NULL, &v) == 0)
        fprintf(stderr, "[stats] memory.peak    %lld bytes (%.1f MiB)\n",
                v, (double)v / (1024.0 * 1024.0));
    if (read_stat(cg->path, "cpu.stat", "usage_usec", &v) == 0)
        fprintf(stderr, "[stats] cpu.usage_usec %lld (%.2f s)\n", v, (double)v / 1e6);
    if (read_stat(cg->path, "pids.peak", NULL, &v) == 0)
        fprintf(stderr, "[stats] pids.peak      %lld\n", v);
    if (read_stat(cg->path, "memory.events", "oom_kill", &v) == 0)
        fprintf(stderr, "[stats] oom_kill       %lld\n", v);
}

long long cgroup_oom_kills(const struct cgroup *cg)
{
    long long v;

    return read_stat(cg->path, "memory.events", "oom_kill", &v) == 0 ? v : 0;
}

/* --------------------------------- cleanup --------------------------------- */

/*
 * A cgroup can only be removed when it has no processes. Right after the
 * container's init is reaped, its other processes may still be exiting, so
 * rmdir can fail with EBUSY for a moment: retry for up to a second.
 */
void cgroup_remove(struct cgroup *cg)
{
    struct timespec delay = { 0, REMOVE_DELAY_NS };
    int i;

    if (!cg->created)
        return;
    for (i = 0; i < REMOVE_TRIES; i++) {
        if (rmdir(cg->path) == 0) {
            cg->created = 0;
            return;
        }
        if (errno != EBUSY)
            break;
        nanosleep(&delay, NULL);
    }
    fprintf(stderr, "minibox: could not remove %s: %s (remove it with: sudo rmdir %s)\n",
            cg->path, strerror(errno), cg->path);
}
