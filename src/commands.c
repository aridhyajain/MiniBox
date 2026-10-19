/*
 * commands.c - the minibox subcommands: run, ps, logs, stop, rm.
 *
 * OS concept: these commands are separate short-lived processes. They never
 * talk to the monitor directly; they read the state files it keeps up to date
 * (state.c) and use signals (kill) to act on the container's init process.
 *
 * Why `stop` must usually escalate to SIGKILL: the first process of a PID
 * namespace is its init. The kernel only delivers a signal to init from an
 * ancestor namespace (the host) if init installed a handler for it; SIGKILL
 * and SIGSTOP are the exceptions and always work. `sleep` or `sh` as init have
 * no SIGTERM handler, so SIGTERM is silently dropped and only SIGKILL stops them.
 */
#define _GNU_SOURCE
#include "commands.h"
#include "cgroup.h"
#include "monitor.h"
#include "net.h"
#include "options.h"
#include "state.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define STOP_DEFAULT_SECONDS 5
#define STOP_MAX_SECONDS     3600
#define KILL_WAIT_SECONDS    5          /* how long to wait after SIGKILL */
#define POLL_NS              100000000L /* 100 ms between state checks */
#define FOLLOW_NS            200000000L /* 200 ms between log polls */
#define COL_NAME_MAX         16
#define COL_CMD_MAX          30

static void sleep_ns(long ns)
{
    struct timespec ts = { 0, ns };

    nanosleep(&ts, NULL);
}

/* Namespaces, mounts and pivot_root all need CAP_SYS_ADMIN, i.e. root. */
static int require_root(void)
{
    if (geteuid() != 0) {
        fprintf(stderr, "minibox: must be run as root (try: sudo ./minibox ...)\n");
        return -1;
    }
    return 0;
}

/* ================================== run ================================== */

static void usage_run(void)
{
    fprintf(stderr,
            "Usage: minibox run [-d] [--name NAME] [--rootfs DIR] [--hostname NAME]\n"
            "                   [--net none|veth] [--memory N[K|M|G]] [--cpus X]\n"
            "                   [--pids N] [--stats] -- COMMAND [ARGS...]\n"
            "  -d               run in the background and print only the container ID\n"
            "  --name NAME      give the container a unique name (usable instead of the ID)\n"
            "  --rootfs DIR     root filesystem directory (default: %s)\n"
            "  --hostname NAME  container hostname (default: %s)\n"
            "  --net MODE       none = loopback only (default),\n"
            "                   veth = loopback + veth link, container 10.0.n.2 <-> host 10.0.n.1\n"
            "                   (n = lowest subnet number not used by another container)\n"
            "  --memory SIZE    memory limit, e.g. 64M (cgroup memory.max, no swap)\n"
            "  --cpus X         CPU limit in cores, e.g. 0.5 or 2 (cgroup cpu.max)\n"
            "  --pids N         maximum number of processes (cgroup pids.max)\n"
            "  --stats          print cgroup usage (memory, cpu, pids) after exit\n"
            "Without --memory/--cpus/--pids no cgroup is created.\n"
            "Must be run as root (sudo).\n",
            DEFAULT_ROOTFS, DEFAULT_HOSTNAME);
}

int cmd_run(int argc, char **argv)
{
    struct run_options opts;
    struct container_state st;
    char rootfs_abs[PATH_MAX];
    int status;

    if (options_parse_run(argc, argv, &opts) == -1) {
        usage_run();
        return 1;
    }
    if (require_root() == -1)
        return 1;
    if (options_validate_rootfs(opts.cfg.rootfs, rootfs_abs) == -1)
        return 1;
    opts.cfg.rootfs = rootfs_abs;
    if (opts.cfg.stats && !cgroup_limits_set(&opts.cfg.limits))
        fprintf(stderr, "minibox: warning: --stats needs --memory, --cpus or --pids "
                "(no cgroup is created without limits, so there are no stats)\n");

    state_init_from_config(&st, &opts.cfg, opts.name);
    if (state_create(&st) == -1)
        return 1;
    opts.cfg.id = st.id;
    opts.cfg.net_index = state_net_index(&st) < 0 ? 0 : state_net_index(&st);

    if (opts.detach) {
        if (monitor_start_detached(&opts.cfg, &st) != 0)
            return 1;
        printf("%s\n", st.id);
        return fflush(stdout) == 0 ? 0 : 1;
    }
    status = monitor_run_foreground(&opts.cfg, &st);
    return status == -1 ? 1 : status;
}

/* ================================== ps ================================== */

static void usage_ps(void)
{
    fprintf(stderr,
            "Usage: minibox ps [-a] [--json]\n"
            "  -a       also show containers that have exited\n"
            "  --json   print a JSON array instead of a table\n"
            "Shows running containers; 'dead' means the monitor process vanished.\n");
}

/* Copy src to dst, turn control characters into spaces, shorten to max with "...". */
static void ellipsize(char *dst, size_t size, const char *src, size_t max)
{
    size_t i, len = strlen(src);

    if (len > max)
        snprintf(dst, size, "%.*s...", (int)max - 3, src);
    else
        snprintf(dst, size, "%s", src);
    for (i = 0; dst[i] != '\0'; i++)
        if ((unsigned char)dst[i] < 0x20)
            dst[i] = ' ';
}

static void format_age(char *buf, size_t size, long long seconds)
{
    if (seconds < 0)
        seconds = 0;
    if (seconds < 60)
        snprintf(buf, size, "%llds ago", seconds);
    else if (seconds < 3600)
        snprintf(buf, size, "%lldm ago", seconds / 60);
    else if (seconds < 86400)
        snprintf(buf, size, "%lldh ago", seconds / 3600);
    else
        snprintf(buf, size, "%lldd ago", seconds / 86400);
}

/* 67108864 -> "64M"; sizes that are not a whole K/M/G stay in bytes. */
static void format_size(char *buf, size_t size, long long bytes)
{
    if (bytes % (1LL << 30) == 0)
        snprintf(buf, size, "%lldG", bytes >> 30);
    else if (bytes % (1LL << 20) == 0)
        snprintf(buf, size, "%lldM", bytes >> 20);
    else if (bytes % (1LL << 10) == 0)
        snprintf(buf, size, "%lldK", bytes >> 10);
    else
        snprintf(buf, size, "%lld", bytes);
}

/* "mem=64M cpu=0.5 pids=32", or "-" when no limit is set. */
static void format_limits(char *buf, size_t size, const struct container_state *st)
{
    char part[64], mem[32];
    size_t used = 0;

    buf[0] = '\0';
    if (st->memory > 0) {
        format_size(mem, sizeof(mem), st->memory);
        snprintf(part, sizeof(part), "mem=%s", mem);
        used += (size_t)snprintf(buf + used, size - used, "%s%s", used ? " " : "", part);
    }
    if (st->cpus > 0 && used < size) {
        snprintf(part, sizeof(part), "cpu=%g", st->cpus);
        used += (size_t)snprintf(buf + used, size - used, "%s%s", used ? " " : "", part);
    }
    if (st->pids > 0 && used < size)
        snprintf(buf + used, size - used, "%spids=%ld", used ? " " : "", st->pids);
    if (buf[0] == '\0')
        snprintf(buf, size, "-");
}

/* Without -a: everything that is not cleanly finished (running and dead). */
static int ps_visible(const struct container_state *st, int all)
{
    return all || strcmp(state_effective_status(st), STATUS_EXITED) != 0;
}

static void print_table_row(const struct container_state *st, long long now)
{
    const char *status = state_effective_status(st);
    char name[COL_NAME_MAX + 4], cmd[COL_CMD_MAX + 4], age[32], limits[96], pid[24];

    ellipsize(name, sizeof(name), st->name[0] ? st->name : "-", COL_NAME_MAX);
    ellipsize(cmd, sizeof(cmd), st->command, COL_CMD_MAX);
    format_age(age, sizeof(age), now - st->created_at);
    format_limits(limits, sizeof(limits), st);
    if (st->pid > 0 && strcmp(status, STATUS_EXITED) != 0)
        snprintf(pid, sizeof(pid), "%ld", st->pid);
    else
        snprintf(pid, sizeof(pid), "-");
    printf("%-12s %-*s %-8s %-8s %-*s %-10s %s\n", st->id, COL_NAME_MAX, name, status, pid,
           COL_CMD_MAX, cmd, age, limits);
}

static void print_table(const struct container_state *all, size_t n, int show_all)
{
    long long now = (long long)time(NULL);
    size_t i;

    printf("%-12s %-*s %-8s %-8s %-*s %-10s %s\n", "ID", COL_NAME_MAX, "NAME", "STATUS", "PID",
           COL_CMD_MAX, "COMMAND", "CREATED", "LIMITS");
    for (i = 0; i < n; i++)
        if (ps_visible(&all[i], show_all))
            print_table_row(&all[i], now);
}

/* JSON string with the escapes the format requires (", \, control characters). */
static void json_string(FILE *f, const char *s)
{
    fputc('"', f);
    for (; *s != '\0'; s++) {
        unsigned char c = (unsigned char)*s;

        switch (c) {
        case '"':  fputs("\\\"", f); break;
        case '\\': fputs("\\\\", f); break;
        case '\n': fputs("\\n", f);  break;
        case '\r': fputs("\\r", f);  break;
        case '\t': fputs("\\t", f);  break;
        default:
            if (c < 0x20)
                fprintf(f, "\\u%04x", c);
            else
                fputc(c, f);
        }
    }
    fputc('"', f);
}

static void json_str_field(FILE *f, const char *key, const char *value, const char *sep)
{
    fprintf(f, "\"%s\":", key);
    json_string(f, value);
    fputs(sep, f);
}

static void print_json_object(FILE *f, const struct container_state *st)
{
    fputs("  {", f);
    json_str_field(f, "id", st->id, ",");
    json_str_field(f, "name", st->name, ",");
    json_str_field(f, "status", state_effective_status(st), ",");
    fprintf(f, "\"pid\":%ld,\"monitor_pid\":%ld,\"exit_code\":%d,", st->pid, st->monitor_pid,
            st->exit_code);
    fprintf(f, "\"created_at\":%lld,\"started_at\":%lld,\"finished_at\":%lld,", st->created_at,
            st->started_at, st->finished_at);
    json_str_field(f, "command", st->command, ",");
    json_str_field(f, "rootfs", st->rootfs, ",");
    json_str_field(f, "hostname", st->hostname, ",");
    json_str_field(f, "net", st->net, ",");
    fprintf(f, "\"memory\":%lld,\"cpus\":%g,\"pids\":%ld,", st->memory, st->cpus, st->pids);
    json_str_field(f, "ip", st->ip, "}");
}

static void print_json(const struct container_state *all, size_t n, int show_all)
{
    size_t i, printed = 0;

    fputs("[", stdout);
    for (i = 0; i < n; i++) {
        if (!ps_visible(&all[i], show_all))
            continue;
        fputs(printed++ ? ",\n" : "\n", stdout);
        print_json_object(stdout, &all[i]);
    }
    fputs(printed ? "\n]\n" : "]\n", stdout);
}

int cmd_ps(int argc, char **argv)
{
    struct container_state *all;
    size_t n;
    int i, show_all = 0, json = 0;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-a") == 0)
            show_all = 1;
        else if (strcmp(argv[i], "--json") == 0)
            json = 1;
        else {
            usage_ps();
            return 1;
        }
    }
    if (state_list(&all, &n) == -1)
        return 1;
    if (json)
        print_json(all, n, show_all);
    else
        print_table(all, n, show_all);
    free(all);
    return fflush(stdout) == 0 ? 0 : 1;
}

/* ================================= logs ================================= */

static void usage_logs(void)
{
    fprintf(stderr,
            "Usage: minibox logs [-f] <id|name>\n"
            "  -f   keep following new output until the container exits (Ctrl-C to stop)\n");
}

/* Is the container still running (and its monitor alive)? A removed one is not. */
static int is_running(const char *id)
{
    struct container_state st;

    return state_load(id, &st) == 0 &&
           strcmp(state_effective_status(&st), STATUS_RUNNING) == 0;
}

/*
 * Follow like `tail -f`: the descriptor keeps its file offset, so every
 * state_copy_fd() continues where the last one stopped. Whether the container
 * is still running is checked BEFORE copying, so output written just before it
 * exited is still printed by the last round.
 */
static int follow_log(const char *id, int fd)
{
    for (;;) {
        int running = is_running(id);

        if (state_copy_fd(fd, STDOUT_FILENO) == -1)
            return -1;
        if (!running)
            return 0;
        sleep_ns(FOLLOW_NS);
    }
}

int cmd_logs(int argc, char **argv)
{
    struct container_state st;
    char path[PATH_MAX];
    const char *key = NULL;
    int i, fd, follow = 0, ret;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-f") == 0)
            follow = 1;
        else if (key == NULL && argv[i][0] != '-')
            key = argv[i];
        else {
            usage_logs();
            return 1;
        }
    }
    if (key == NULL) {
        usage_logs();
        return 1;
    }
    if (state_find(key, &st) == -1)
        return 1;
    if (state_path(st.id, "log", path, sizeof(path)) == -1)
        return 1;
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd == -1) {
        if (errno == ENOENT)
            fprintf(stderr, "minibox: container %s has no log (only 'run -d' keeps one)\n", st.id);
        else
            fprintf(stderr, "minibox: open %s: %s\n", path, strerror(errno));
        return 1;
    }
    ret = follow ? follow_log(st.id, fd) : state_copy_fd(fd, STDOUT_FILENO);
    close(fd);
    return ret == 0 ? 0 : 1;
}

/* ================================= stop ================================= */

static void usage_stop(void)
{
    fprintf(stderr,
            "Usage: minibox stop [--time N] <id|name>\n"
            "  --time N   seconds to wait after SIGTERM before sending SIGKILL (default %d)\n"
            "Note: a PID 1 without a SIGTERM handler (e.g. plain 'sleep' or 'sh') ignores\n"
            "SIGTERM, so stop usually ends up using SIGKILL.\n",
            STOP_DEFAULT_SECONDS);
}

/* Has the container left the running state (exited, dead or removed)? */
static int has_stopped(const char *id)
{
    return !is_running(id);
}

/* Poll every 100 ms for up to `seconds`. Returns 1 as soon as it has stopped. */
static int wait_stopped(const char *id, int seconds)
{
    long ticks = (long)seconds * 10, i;

    for (i = 0; i < ticks; i++) {
        sleep_ns(POLL_NS);
        if (has_stopped(id))
            return 1;
    }
    return has_stopped(id);
}

/* kill() that treats "no such process" as success (it is already gone). */
static int send_signal(pid_t pid, int sig)
{
    if (kill(pid, sig) == -1 && errno != ESRCH) {
        fprintf(stderr, "minibox: kill(%d, %s): %s\n", (int)pid, strsignal(sig),
                strerror(errno));
        return -1;
    }
    return 0;
}

/*
 * SIGTERM, wait up to `seconds`, then SIGKILL. The monitor notices the exit,
 * cleans up and records status=exited; we wait for that. Returns 0 if the
 * container stopped, -1 otherwise (message printed).
 */
static int stop_container(const struct container_state *st, int seconds)
{
    if (st->pid <= 0) {
        fprintf(stderr, "minibox: container %s has no PID yet\n", st->id);
        return -1;
    }
    if (send_signal((pid_t)st->pid, SIGTERM) == -1)
        return -1;
    if (wait_stopped(st->id, seconds)) {
        printf("stopped %s (SIGTERM)\n", st->id);
        return 0;
    }
    if (send_signal((pid_t)st->pid, SIGKILL) == -1)
        return -1;
    if (!wait_stopped(st->id, KILL_WAIT_SECONDS)) {
        fprintf(stderr, "minibox: container %s did not stop after SIGKILL\n", st->id);
        return -1;
    }
    printf("stopped %s (SIGKILL after %ds)\n", st->id, seconds);
    return 0;
}

/* Handle a container that is not running. Returns 1 if the caller is done (status in *ret). */
static int stop_not_running(const struct container_state *st, const char *status, int *ret)
{
    if (strcmp(status, STATUS_RUNNING) == 0)
        return 0;
    if (strcmp(status, STATUS_DEAD) == 0) {
        fprintf(stderr, "minibox: container %s is dead (its monitor process is gone); "
                "use 'minibox rm %s' to clean it up\n", st->id, st->id);
        *ret = 1;
    } else {
        printf("container %s is not running (exited with code %d)\n", st->id, st->exit_code);
        *ret = 0;
    }
    return 1;
}

static int parse_seconds(const char *s, int *out)
{
    char *end;
    long v;

    errno = 0;
    v = strtol(s, &end, 10);
    if (end == s || *end != '\0' || errno == ERANGE || v < 0 || v > STOP_MAX_SECONDS) {
        fprintf(stderr, "minibox: invalid --time '%s' (use 0 to %d)\n", s, STOP_MAX_SECONDS);
        return -1;
    }
    *out = (int)v;
    return 0;
}

int cmd_stop(int argc, char **argv)
{
    struct container_state st;
    const char *key = NULL;
    int i, seconds = STOP_DEFAULT_SECONDS, ret;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--time") == 0 && i + 1 < argc) {
            if (parse_seconds(argv[++i], &seconds) == -1)
                return 1;
        } else if (key == NULL && argv[i][0] != '-') {
            key = argv[i];
        } else {
            usage_stop();
            return 1;
        }
    }
    if (key == NULL) {
        usage_stop();
        return 1;
    }
    if (state_find(key, &st) == -1)
        return 1;
    if (stop_not_running(&st, state_effective_status(&st), &ret))
        return ret;
    return stop_container(&st, seconds) == 0 ? 0 : 1;
}

/* ================================== rm ================================== */

static void usage_rm(void)
{
    fprintf(stderr,
            "Usage: minibox rm [-f] <id|name>\n"
            "  -f   stop the container first if it is running\n"
            "Removes the container's state and log. For a dead container (monitor gone)\n"
            "it also kills a surviving init, removes its cgroup and deletes its veth.\n");
}

/* Wait up to 5 s for a killed process to vanish. */
static void wait_gone(pid_t pid)
{
    int i;

    for (i = 0; i < KILL_WAIT_SECONDS * 10 && process_alive(pid); i++)
        sleep_ns(POLL_NS);
}

/*
 * The monitor died, so nobody cleaned up. The init may still run (kill it, but
 * only if it really is a PID-namespace init: the PID could have been reused),
 * and the cgroup leaf and the host veth may be left over.
 */
static void clean_dead(const struct container_state *st)
{
    pid_t pid = (pid_t)st->pid;

    if (pid > 0 && process_alive(pid) && state_pid_is_ns_init(pid)) {
        if (send_signal(pid, SIGKILL) == 0)
            wait_gone(pid);
    }
    cgroup_remove_leftover(st->id);
    if (pid > 0)
        net_veth_cleanup(pid);
}

/* Decide what must happen before the state can be deleted. 0 = go ahead, -1 = refuse. */
static int prepare_removal(const struct container_state *st, int force)
{
    const char *status = state_effective_status(st);

    if (strcmp(status, STATUS_DEAD) == 0) {
        clean_dead(st);
        return 0;
    }
    if (strcmp(status, STATUS_RUNNING) != 0)
        return 0;
    if (!force) {
        fprintf(stderr, "minibox: container %s is running; stop it first or use 'rm -f'\n",
                st->id);
        return -1;
    }
    return stop_container(st, STOP_DEFAULT_SECONDS);
}

int cmd_rm(int argc, char **argv)
{
    struct container_state st;
    const char *key = NULL;
    int i, force = 0;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-f") == 0)
            force = 1;
        else if (key == NULL && argv[i][0] != '-')
            key = argv[i];
        else {
            usage_rm();
            return 1;
        }
    }
    if (key == NULL) {
        usage_rm();
        return 1;
    }
    if (state_find(key, &st) == -1)
        return 1;
    if (prepare_removal(&st, force) == -1)
        return 1;
    if (state_remove(st.id) == -1)
        return 1;
    printf("%s\n", st.id);
    return 0;
}
