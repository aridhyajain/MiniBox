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
 *
 * Dying together: if `minibox exec` is killed, an orphaned command would be
 * reparented to the container's PID 1 and keep running (and keep counting
 * against the container's pids limit). So the child asks the kernel to
 * SIGKILL it when its parent dies (PR_SET_PDEATHSIG), and when stdin is not a
 * terminal (scripts, the web UI) the command also gets its own process group,
 * so one signal sent to `minibox exec` is turned into a SIGKILL for the
 * whole group, grandchildren included.
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
#include <sys/prctl.h>
#include <sys/types.h>
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

/*
 * Set by the signal handler while a non-interactive command runs: the child's
 * PID (which is also its process group ID) and the signal that arrived. This
 * is the one piece of global state, because a signal handler cannot get
 * arguments.
 */
static volatile pid_t g_child_pgid = 0;
static volatile sig_atomic_t g_signal = 0;

/* Signals that stop a non-interactive `minibox exec` and take the command with it. */
static const int FORWARDED[] = { SIGTERM, SIGINT, SIGHUP };
#define N_FORWARDED (sizeof(FORWARDED) / sizeof(FORWARDED[0]))

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
            "  If minibox exec itself is killed, the command is killed too (when stdin is not a\n"
            "  terminal, SIGTERM/SIGINT/SIGHUP also kill the command's whole process group).\n"
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

/*
 * Make sure the command cannot outlive `minibox exec`.
 *
 * PR_SET_PDEATHSIG: the kernel sends us SIGKILL when the parent process dies.
 * The parent may already have died before the prctl call, in which case the
 * signal would never come, so the parent is checked afterwards. Our parent is
 * on the host, outside the container's PID namespace, and a process cannot
 * see a parent outside its namespace: getppid() is 0 while it is alive and
 * becomes the container's init (non-zero) once we have been orphaned.
 * The setting is kept across execve(), but not inherited by the command's own
 * children, which is why group_mode also exists.
 */
static int die_with_parent(void)
{
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) == -1) {
        perror("prctl(PR_SET_PDEATHSIG)");
        return -1;
    }
    if (getppid() != 0) {
        fprintf(stderr, "minibox: exec: minibox exec has already exited\n");
        return -1;
    }
    return 0;
}

/* Runs as a child inside the container's PID namespace. Never returns. */
static void child_main(const struct container_state *st, struct target *t,
                       const char *term, char *const *argv, int group_mode)
{
    if (die_with_parent() == -1)
        _exit(1);
    /* Own process group: the parent can signal the command and all its children at once. */
    if (group_mode && setpgid(0, 0) == -1) {
        perror("setpgid");
        _exit(1);
    }
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
    if (g_signal != 0)                  /* we were told to stop: report 128+signal */
        return 128 + (int)g_signal;
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
 * Signal handler for SIGTERM/SIGINT/SIGHUP in group mode. Only async-signal-safe
 * calls are allowed here: kill() and plain stores. SIGKILL goes to the whole
 * process group (the command and everything it started) and to the child
 * itself, in case it never became a group leader. The main flow then reaps
 * the child in waitpid() and exits with 128+signal.
 */
static void kill_command(int sig)
{
    int saved_errno = errno;

    g_signal = sig;
    if (g_child_pgid > 0) {
        kill(-g_child_pgid, SIGKILL);
        kill(g_child_pgid, SIGKILL);
    }
    errno = saved_errno;
}

static int install_kill_handlers(struct sigaction old[N_FORWARDED])
{
    struct sigaction act;
    size_t i;

    memset(&act, 0, sizeof(act));
    act.sa_handler = kill_command;
    sigemptyset(&act.sa_mask);
    for (i = 0; i < N_FORWARDED; i++) {
        if (sigaction(FORWARDED[i], &act, &old[i]) == -1) {
            perror("sigaction(forwarded signal)");
            while (i-- > 0)
                sigaction(FORWARDED[i], &old[i], NULL);
            return -1;
        }
    }
    return 0;
}

static void restore_kill_handlers(const struct sigaction old[N_FORWARDED])
{
    size_t i;

    for (i = 0; i < N_FORWARDED; i++)
        sigaction(FORWARDED[i], &old[i], NULL);
}

/*
 * Join the PID namespace (affects only future children), fork, and let the
 * child do the rest. Returns the command's exit status, or -1 on a minibox error.
 *
 * Two modes:
 *  - stdin is a terminal (interactive `exec -- sh`): the command stays in our
 *    foreground process group so job control and Ctrl-C work as in any shell;
 *    we ignore SIGINT/SIGQUIT while waiting. PR_SET_PDEATHSIG still applies.
 *  - stdin is not a terminal (scripts, the web UI): "group mode", the command
 *    is its own process group leader and SIGTERM/SIGINT/SIGHUP sent to us kill
 *    that whole group.
 * The three signals are blocked across fork() so that a signal arriving early
 * waits until the handler is installed and the child's PID is known.
 */
static int run_in_container(const struct container_state *st, struct target *t,
                            const char *term, char *const *argv)
{
    struct sigaction old_int, old_quit, old_fwd[N_FORWARDED];
    sigset_t block, old_mask;
    int group_mode = !isatty(STDIN_FILENO);
    pid_t pid;
    size_t i;
    int result;

    for (i = 0; i < N_NAMESPACES; i++)
        if (NAMESPACES[i].nstype == CLONE_NEWPID && join_namespace(t, i) == -1)
            return -1;
    sigemptyset(&block);
    for (i = 0; i < N_FORWARDED; i++)
        sigaddset(&block, FORWARDED[i]);
    if (sigprocmask(SIG_BLOCK, &block, &old_mask) == -1) {
        perror("sigprocmask");
        return -1;
    }
    if (!group_mode && ignore_interrupts(&old_int, &old_quit) == -1) {
        sigprocmask(SIG_SETMASK, &old_mask, NULL);
        return -1;
    }
    g_child_pgid = 0;
    g_signal = 0;
    pid = fork();
    if (pid == -1) {
        perror("fork");
        if (!group_mode)
            restore_interrupts(&old_int, &old_quit);
        sigprocmask(SIG_SETMASK, &old_mask, NULL);
        return -1;
    }
    if (pid == 0) {
        if (!group_mode)
            restore_interrupts(&old_int, &old_quit);
        if (sigprocmask(SIG_SETMASK, &old_mask, NULL) == -1) {
            perror("sigprocmask");
            _exit(1);
        }
        child_main(st, t, term, argv, group_mode);
    }
    if (group_mode) {
        /* The child does this too; doing it here as well closes the race either way. */
        if (setpgid(pid, pid) == -1 && errno != EACCES && errno != ESRCH)
            perror("setpgid(child)");
        g_child_pgid = pid;
        if (install_kill_handlers(old_fwd) == -1) {
            kill(-pid, SIGKILL);
            kill(pid, SIGKILL);
            wait_for_child(pid);
            sigprocmask(SIG_SETMASK, &old_mask, NULL);
            return -1;
        }
    }
    /* a signal that arrived since fork() is delivered now, with the handler in place */
    if (sigprocmask(SIG_SETMASK, &old_mask, NULL) == -1)
        perror("sigprocmask(restore)");
    result = wait_for_child(pid);
    if (group_mode)
        restore_kill_handlers(old_fwd);
    else
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
