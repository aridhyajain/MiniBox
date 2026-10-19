/*
 * exec.c - `minibox exec`: run a command inside an already running container.
 *
 * OS concept: a namespace lives as long as something uses it, and every
 * process has one symlink per namespace in /proc/<pid>/ns/. Opening such a
 * link gives a file descriptor for that namespace, and setns(fd) moves the
 * calling process into it. So "entering a container" means: take the
 * container's init PID, open its ns files and join them one by one.
 *
 * Two details make this different from `run` (which uses clone()):
 *  - Joining a PID namespace does not move the caller; only its future
 *    children land in it. So we setns(pid) and then fork().
 *  - After joining the mount namespace /proc and /sys/fs/cgroup are the
 *    container's, so everything that needs the host's view (the ns files, the
 *    cgroup tree) is opened or written BEFORE the mount namespace is joined.
 */
#define _GNU_SOURCE
#include "exec.h"
#include "cgroup.h"
#include "commands.h"
#include "container.h"
#include "state.h"

#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define DEFAULT_TERM "xterm"

/* The namespaces to enter, in the order they are joined: mnt must be last. */
struct ns_entry {
    const char *name;       /* file in /proc/<pid>/ns/ */
    int nstype;             /* CLONE_NEW* flag, so setns() checks the type */
};

static const struct ns_entry NAMESPACES[] = {
    { "ipc", CLONE_NEWIPC },
    { "uts", CLONE_NEWUTS },
    { "net", CLONE_NEWNET },
    { "pid", CLONE_NEWPID },
    { "mnt", CLONE_NEWNS },
};
#define N_NAMESPACES (sizeof(NAMESPACES) / sizeof(NAMESPACES[0]))

/* Descriptors opened on the container's init before anything is joined. */
struct target {
    int ns_fd[N_NAMESPACES];
    int root_fd;            /* /proc/<pid>/root: the container's root directory */
};

static void usage_exec(void)
{
    fprintf(stderr,
            "Usage: minibox exec <id|name> -- COMMAND [ARGS...]\n"
            "  Runs COMMAND inside a running container, sharing its namespaces and cgroup.\n"
            "  stdin/stdout/stderr are yours, so 'minibox exec <id> -- sh' is an interactive shell.\n"
            "  The exit status is the command's (128+N if it was killed by signal N).\n"
            "Must be run as root (sudo).\n");
}

/* ------------------------------ lookup ------------------------------ */

/*
 * Only a running container can be entered. A dead one (monitor gone) may
 * still have an init, but its state can no longer be trusted: use `rm`.
 * Returns 0, or -1 with a message.
 */
static int find_running(const char *key, struct container_state *st)
{
    const char *status;

    if (state_find(key, st) == -1)
        return -1;
    status = state_effective_status(st);
    if (strcmp(status, STATUS_DEAD) == 0) {
        fprintf(stderr, "minibox: container %s is dead (its monitor process is gone); "
                "use 'minibox rm %s' to clean it up\n", st->id, st->id);
        return -1;
    }
    if (strcmp(status, STATUS_RUNNING) != 0) {
        fprintf(stderr, "minibox: container %s is not running (status: %s)\n", st->id, status);
        return -1;
    }
    /* the PID must still be the init of a PID namespace, not a reused PID */
    if (st->pid <= 0 || !state_pid_is_ns_init((pid_t)st->pid)) {
        fprintf(stderr, "minibox: container %s has no running init process\n", st->id);
        return -1;
    }
    return 0;
}

/* ------------------------- namespace descriptors ------------------------- */

static void close_target(struct target *t)
{
    size_t i;

    for (i = 0; i < N_NAMESPACES; i++) {
        if (t->ns_fd[i] != -1)
            close(t->ns_fd[i]);
        t->ns_fd[i] = -1;
    }
    if (t->root_fd != -1)
        close(t->root_fd);
    t->root_fd = -1;
}

static int open_path(const char *path)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);

    if (fd == -1) {
        if (errno == ENOENT)
            fprintf(stderr, "minibox: %s: the container has just exited\n", path);
        else
            fprintf(stderr, "minibox: open %s: %s\n", path, strerror(errno));
    }
    return fd;
}

/*
 * Open the ns files in /proc/<pid>/ns/ and /proc/<pid>/root. An open descriptor keeps the
 * namespace alive and stays valid after we have joined the mount namespace,
 * where /proc no longer shows the host's processes. Returns 0, or -1 (all
 * descriptors closed).
 */
static int open_target(pid_t pid, struct target *t)
{
    char path[64];
    size_t i;

    for (i = 0; i < N_NAMESPACES; i++)
        t->ns_fd[i] = -1;
    t->root_fd = -1;
    for (i = 0; i < N_NAMESPACES; i++) {
        snprintf(path, sizeof(path), "/proc/%d/ns/%s", (int)pid, NAMESPACES[i].name);
        t->ns_fd[i] = open_path(path);
        if (t->ns_fd[i] == -1) {
            close_target(t);
            return -1;
        }
    }
    snprintf(path, sizeof(path), "/proc/%d/root", (int)pid);
    t->root_fd = open_path(path);
    if (t->root_fd == -1) {
        close_target(t);
        return -1;
    }
    return 0;
}

static int join_namespace(const struct target *t, size_t i)
{
    if (setns(t->ns_fd[i], NAMESPACES[i].nstype) == -1) {
        fprintf(stderr, "minibox: setns(%s): %s\n", NAMESPACES[i].name, strerror(errno));
        return -1;
    }
    return 0;
}

/* ------------------------------- child ------------------------------- */

/*
 * The cgroup tree is a host path, and inside the container /sys/fs/cgroup is
 * not it, so this must happen before the mount namespace is joined. Writing
 * the PID to cgroup.procs makes the container's limits apply to us.
 */
static int join_cgroup(const char *id)
{
    return cgroup_join_leaf(id, getpid()) == -1 ? -1 : 0;
}

/*
 * Make the container's root directory ours. setns(mnt) already switches to
 * the container's mount table; fchdir + chroot(".") also makes sure that our
 * root is the one saved before, and chdir("/") gives a defined start directory.
 */
static int enter_root(int root_fd)
{
    if (fchdir(root_fd) == -1) {
        perror("fchdir(container root)");
        return -1;
    }
    if (chroot(".") == -1) {
        perror("chroot(.)");
        return -1;
    }
    if (chdir("/") == -1) {
        perror("chdir(/)");
        return -1;
    }
    return 0;
}

/* Same environment as `run`, but built from scratch: nothing of the host leaks in. */
static int set_environment(const char *term)
{
    if (clearenv() != 0) {
        perror("clearenv");
        return -1;
    }
    if (setenv("PATH", DEFAULT_PATH, 1) == -1 || setenv("HOME", "/root", 1) == -1 ||
        setenv("TERM", term, 1) == -1) {
        perror("setenv");
        return -1;
    }
    return 0;
}

/* Everything between fork() and execvp(). Returns 0, or -1 with a message. */
static int child_setup(const struct container_state *st, const struct target *t,
                       const char *term)
{
    size_t i;

    if (join_cgroup(st->id) == -1)
        return -1;
    for (i = 0; i < N_NAMESPACES; i++) {
        if (NAMESPACES[i].nstype == CLONE_NEWPID)
            continue;                   /* joined by the parent, before fork() */
        if (join_namespace(t, i) == -1)
            return -1;
    }
    if (enter_root(t->root_fd) == -1)
        return -1;
    return set_environment(term);
}

/* Runs as a child inside the container's PID namespace. Never returns. */
static void child_main(const struct container_state *st, struct target *t,
                       const char *term, char *const *argv)
{
    if (child_setup(st, t, term) == -1)
        _exit(1);
    close_target(t);                    /* do not leak host descriptors into the command */
    execvp(argv[0], argv);
    fprintf(stderr, "exec %s: %s\n", argv[0], strerror(errno));
    _exit(127);                         /* shell convention: command not found / not executable */
}

/* ------------------------------- parent ------------------------------- */

/* Wait for the child; shell-style status (128+N if killed by signal N). */
static int wait_for_child(pid_t pid)
{
    int status;

    while (waitpid(pid, &status, 0) == -1) {
        if (errno != EINTR) {
            perror("waitpid");
            return 1;
        }
    }
    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);
    return 1;
}

/*
 * Ctrl-C goes to the whole foreground process group, i.e. to us and to the
 * command. The command decides what to do with it (a shell ignores it, `sleep`
 * dies); we must outlive it to report its status, so we ignore SIGINT/SIGQUIT
 * while waiting. fork() children inherit "ignore", so the child resets them.
 */
static int ignore_interrupts(struct sigaction *old_int, struct sigaction *old_quit)
{
    struct sigaction ign;

    memset(&ign, 0, sizeof(ign));
    ign.sa_handler = SIG_IGN;
    sigemptyset(&ign.sa_mask);
    if (sigaction(SIGINT, &ign, old_int) == -1) {
        perror("sigaction(SIGINT)");
        return -1;
    }
    if (sigaction(SIGQUIT, &ign, old_quit) == -1) {
        perror("sigaction(SIGQUIT)");
        sigaction(SIGINT, old_int, NULL);
        return -1;
    }
    return 0;
}

static void restore_interrupts(const struct sigaction *old_int,
                               const struct sigaction *old_quit)
{
    if (sigaction(SIGINT, old_int, NULL) == -1)
        perror("sigaction(restore SIGINT)");
    if (sigaction(SIGQUIT, old_quit, NULL) == -1)
        perror("sigaction(restore SIGQUIT)");
}

/*
 * Join the PID namespace (affects only future children), fork, and let the
 * child do the rest. Returns the command's exit status, or -1 on a minibox error.
 */
static int run_in_container(const struct container_state *st, struct target *t,
                            const char *term, char *const *argv)
{
    struct sigaction old_int, old_quit;
    pid_t pid;
    size_t i;
    int result;

    for (i = 0; i < N_NAMESPACES; i++)
        if (NAMESPACES[i].nstype == CLONE_NEWPID && join_namespace(t, i) == -1)
            return -1;
    if (ignore_interrupts(&old_int, &old_quit) == -1)
        return -1;
    pid = fork();
    if (pid == -1) {
        perror("fork");
        restore_interrupts(&old_int, &old_quit);
        return -1;
    }
    if (pid == 0) {
        restore_interrupts(&old_int, &old_quit);
        child_main(st, t, term, argv);
    }
    result = wait_for_child(pid);
    restore_interrupts(&old_int, &old_quit);
    return result;
}

int cmd_exec(int argc, char **argv)
{
    struct container_state st;
    struct target target;
    const char *term;
    int result;

    /* argv: exec <key> -- COMMAND [ARGS...] */
    if (argc < 2 || argv[1][0] == '-') {
        usage_exec();
        return 1;
    }
    if (argc < 4 || strcmp(argv[2], "--") != 0) {
        fprintf(stderr, "minibox: exec: no command given (use: minibox exec %s -- COMMAND)\n",
                argv[1]);
        return 1;
    }
    if (require_root() == -1)
        return 1;
    if (find_running(argv[1], &st) == -1)
        return 1;
    if (open_target((pid_t)st.pid, &target) == -1)
        return 1;
    term = getenv("TERM");
    result = run_in_container(&st, &target, term != NULL && term[0] != '\0' ? term : DEFAULT_TERM,
                              &argv[3]);
    close_target(&target);
    return result == -1 ? 1 : result;
}
