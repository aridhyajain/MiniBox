/*
 * monitor.c - the long-lived process behind every container.
 *
 * OS concept: a container is only a process (tree) in some namespaces. Someone
 * must stay behind as its parent to wait() for it, otherwise its exit status
 * is lost, and to clean up the cgroup and veth afterwards. That is the monitor.
 * For `minibox run` it is the foreground process itself; for `run -d` it is a
 * background process that outlives the command line that started it.
 *
 * Detaching ("daemonizing") a process takes four steps, all done here:
 *   1. fork(): the parent returns to the shell, the child carries on.
 *   2. setsid(): new session without a controlling terminal, so closing the
 *      terminal (SIGHUP) or Ctrl-C does not reach it.
 *   3. stdin from /dev/null, stdout/stderr to the log file.
 *   4. chdir("/") so it does not keep some directory busy.
 *
 * Start-up report: the parent waits on a pipe. The monitor writes one byte
 * once the container is running; if it dies first, the parent reads EOF.
 */
#define _GNU_SOURCE
#include "monitor.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define EXIT_RUNTIME_ERROR 125      /* container_run() failed: no command exit code exists */
#define MSG_STARTED 'S'

struct start_ctx {
    struct container_state *st;
    int ready_fd;                   /* pipe to the waiting parent, or -1 */
};

/* Write the start byte; a vanished parent must not kill us with SIGPIPE. */
static void tell_parent_started(int fd)
{
    struct sigaction ignore, old;
    char c = MSG_STARTED;

    memset(&ignore, 0, sizeof(ignore));
    ignore.sa_handler = SIG_IGN;
    sigemptyset(&ignore.sa_mask);
    if (sigaction(SIGPIPE, &ignore, &old) == -1) {
        perror("sigaction(SIGPIPE)");
        return;
    }
    if (write(fd, &c, 1) != 1 && errno != EPIPE)
        perror("write(ready pipe)");
    sigaction(SIGPIPE, &old, NULL);
}

/* container_run() calls this when the container has been released to run. */
static void on_started(pid_t pid, void *arg)
{
    struct start_ctx *ctx = arg;

    ctx->st->pid = pid;
    ctx->st->started_at = (long long)time(NULL);
    if (state_save(ctx->st) == -1)
        fprintf(stderr, "minibox: warning: could not record the container PID\n");
    if (ctx->ready_fd >= 0) {
        tell_parent_started(ctx->ready_fd);
        close(ctx->ready_fd);
        ctx->ready_fd = -1;
    }
}

/* Run the container to the end; ready_fd (or -1) is closed on every path. */
static int run_container(struct container_config *cfg, struct container_state *st,
                         int ready_fd)
{
    struct start_ctx ctx = { st, ready_fd };
    int result;

    cfg->on_started = on_started;
    cfg->on_started_arg = &ctx;
    result = container_run(cfg);
    if (ctx.ready_fd >= 0)
        close(ctx.ready_fd);        /* never started: the parent sees EOF */
    return result;
}

/* Written after container_run() returned, i.e. after the cgroup/veth are gone. */
static void record_exit(struct container_state *st, int result)
{
    snprintf(st->status, sizeof(st->status), "%s", STATUS_EXITED);
    st->exit_code = result == -1 ? EXIT_RUNTIME_ERROR : result;
    st->finished_at = (long long)time(NULL);
    if (state_save(st) == -1)
        fprintf(stderr, "minibox: warning: could not record the exit of %s\n", st->id);
}

int monitor_run_foreground(struct container_config *cfg, struct container_state *st)
{
    int result = run_container(cfg, st, -1);

    state_remove(st->id);           /* foreground containers behave like --rm */
    return result;
}

/* ------------------------------ detached ------------------------------ */

/* stdin <- /dev/null, stdout and stderr -> log, then close the originals. */
static int redirect_stdio(int log_fd)
{
    int null_fd = open("/dev/null", O_RDWR | O_CLOEXEC);

    if (null_fd == -1) {
        perror("open(/dev/null)");
        return -1;
    }
    if (dup2(null_fd, STDIN_FILENO) == -1 || dup2(log_fd, STDOUT_FILENO) == -1 ||
        dup2(log_fd, STDERR_FILENO) == -1) {
        perror("dup2");
        return -1;
    }
    close(null_fd);
    close(log_fd);
    return 0;
}

/* Body of the monitor process; never returns. */
static void monitor_main(struct container_config *cfg, struct container_state *st,
                         int ready_fd, int log_fd)
{
    int result;

    if (setsid() == -1) {
        perror("setsid");
        _exit(1);
    }
    if (redirect_stdio(log_fd) == -1 || chdir("/") == -1) {
        perror("monitor setup");
        _exit(1);
    }
    st->monitor_pid = getpid();     /* the parent's PID was only a placeholder */
    if (state_save(st) == -1)
        _exit(1);
    result = run_container(cfg, st, ready_fd);
    record_exit(st, result);
    _exit(0);
}

/* 1 = container started, 0 = monitor died before that, -1 = error. */
static int wait_for_start(int fd)
{
    char c;
    ssize_t n;

    do {
        n = read(fd, &c, 1);
    } while (n == -1 && errno == EINTR);
    if (n == -1) {
        perror("read(ready pipe)");
        return -1;
    }
    return n == 1 && c == MSG_STARTED;
}

/* The container never started: show why (it is in the log), then forget it. */
static void report_failed_start(pid_t monitor, const struct container_state *st)
{
    char path[PATH_MAX];
    int fd;

    if (waitpid(monitor, NULL, 0) == -1)    /* once it has exited the log is final */
        perror("waitpid(monitor)");
    if (state_path(st->id, "log", path, sizeof(path)) == 0 &&
        (fd = open(path, O_RDONLY | O_CLOEXEC)) != -1) {
        state_copy_fd(fd, STDERR_FILENO);
        close(fd);
    }
    fprintf(stderr, "minibox: container failed to start\n");
    state_remove(st->id);
}

/* Create the (empty) log so `minibox logs` works from the first moment. */
static int open_log(const struct container_state *st)
{
    char path[PATH_MAX];
    int fd;

    if (state_path(st->id, "log", path, sizeof(path)) == -1) {
        fprintf(stderr, "minibox: log path too long\n");
        return -1;
    }
    fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd == -1)
        fprintf(stderr, "minibox: open %s: %s\n", path, strerror(errno));
    return fd;
}

int monitor_start_detached(struct container_config *cfg, struct container_state *st)
{
    int ready[2], log_fd, started;
    pid_t pid;

    log_fd = open_log(st);
    if (log_fd == -1)
        return 1;
    if (pipe2(ready, O_CLOEXEC) == -1) {
        perror("pipe2");
        close(log_fd);
        return 1;
    }
    pid = fork();
    if (pid == -1) {
        perror("fork");
        close(log_fd);
        close(ready[0]);
        close(ready[1]);
        state_remove(st->id);
        return 1;
    }
    if (pid == 0) {
        close(ready[0]);
        monitor_main(cfg, st, ready[1], log_fd);
    }
    close(ready[1]);                /* so EOF on ready[0] means "monitor gone" */
    close(log_fd);
    started = wait_for_start(ready[0]);
    close(ready[0]);
    if (started != 1) {
        report_failed_start(pid, st);
        return 1;
    }
    return 0;
}
