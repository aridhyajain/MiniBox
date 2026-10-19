/*
 * minit.c - a tiny init for the container (`minibox run --init`).
 *
 * OS concept: the first process of a PID namespace is its init (PID 1) and the
 * kernel gives it two special jobs and one special rule:
 *   - orphans: when a process dies, its children are reparented to PID 1, and
 *     only their parent can wait() for them. A PID 1 that never calls wait()
 *     leaves them as zombies (dead, but still in the process table).
 *   - signals: the kernel drops a signal sent to PID 1 from outside the
 *     namespace unless PID 1 has a handler for it (or has it blocked, so it
 *     stays pending). A plain `sleep` as PID 1 therefore ignores SIGTERM.
 * So instead of making the user's command PID 1, we make this small program
 * PID 1 and run the command as its child:
 *
 *     minibox-init (PID 1)  --fork/exec-->  the command (PID 2)
 *
 * The init does only three things: forward signals to the command, reap every
 * child it finds, and exit with the command's status. Docker's --init (tini)
 * works the same way.
 *
 * Why signals are blocked and then waited for (sigtimedwait) rather than
 * handled: a blocked signal stays pending, so none is lost or dropped, and the
 * main loop stays an ordinary loop with no async-signal-safety rules.
 */
#define _GNU_SOURCE
#include "minit.h"

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define INIT_NAME         "minibox-init"    /* what /proc/1/comm and ps show (max 15) */
#define EXIT_CANNOT_EXEC  126               /* shell convention: found but not executable */
#define EXIT_NOT_FOUND    127               /* shell convention: command not found / init failed */
#define WAKE_SECONDS      1                 /* safety-net wake-up to reap, see wait_for_event */

/* Signals forwarded to the command. SIGCHLD is handled by the init itself. */
static const int FORWARDED[] = { SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2 };
#define N_FORWARDED (sizeof(FORWARDED) / sizeof(FORWARDED[0]))

/* The signals the init waits for: everything forwarded, plus SIGCHLD. */
static void build_signal_set(sigset_t *set)
{
    size_t i;

    sigemptyset(set);
    sigaddset(set, SIGCHLD);
    for (i = 0; i < N_FORWARDED; i++)
        sigaddset(set, FORWARDED[i]);
}

/* Wait status -> the code a shell would report: exit code, or 128+signal. */
static int exit_code_from_status(int status)
{
    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);
    return EXIT_NOT_FOUND;
}

/*
 * Runs in the forked child; never returns. It inherited the init's blocked
 * mask and the handlers minibox installed for SIGINT/SIGTERM (container.c),
 * so both are put back to the defaults before the command starts.
 */
static void exec_main_child(char *const argv[], const sigset_t *old_mask)
{
    struct sigaction dfl;
    size_t i;

    memset(&dfl, 0, sizeof(dfl));
    dfl.sa_handler = SIG_DFL;
    sigemptyset(&dfl.sa_mask);
    for (i = 0; i < N_FORWARDED; i++)
        if (sigaction(FORWARDED[i], &dfl, NULL) == -1)
            perror("sigaction(default)");
    if (sigaction(SIGCHLD, &dfl, NULL) == -1)
        perror("sigaction(default SIGCHLD)");
    if (sigprocmask(SIG_SETMASK, old_mask, NULL) == -1) {
        perror("sigprocmask(restore)");
        _exit(EXIT_NOT_FOUND);
    }
    execvp(argv[0], argv);
    /* only reached if exec failed: same message as the no-init path */
    fprintf(stderr, "exec %s: %s\n", argv[0], strerror(errno));
    _exit((errno == ENOENT || errno == ENOTDIR) ? EXIT_NOT_FOUND : EXIT_CANNOT_EXEC);
}

/* fork() the command. Returns its PID, or -1 after printing why not. */
static pid_t start_main_child(char *const argv[], const sigset_t *old_mask)
{
    pid_t pid = fork();

    if (pid == -1) {
        fprintf(stderr, "minibox: init: cannot start %s: fork: %s\n", argv[0], strerror(errno));
        return -1;
    }
    if (pid == 0)
        exec_main_child(argv, old_mask);
    return pid;
}

/*
 * Reap every child that has already exited. waitpid(-1) means "any child" -
 * this includes orphans of any depth that were reparented to us. WNOHANG makes
 * it return 0 when children exist but none has exited yet. The loop is needed
 * because SIGCHLD is not queued: several exits can arrive as one signal.
 *
 * Returns 1 if the main child was reaped (its status is stored), 0 if not, and
 * -1 if there are no children at all although the main child was never reaped.
 */
static int reap_children(pid_t main_pid, int *main_status)
{
    int status, reaped_main = 0;
    pid_t pid;

    for (;;) {
        pid = waitpid(-1, &status, WNOHANG);
        if (pid == -1 && errno == EINTR)
            continue;
        if (pid == -1 && errno == ECHILD)
            return reaped_main ? 1 : -1;
        if (pid == -1) {
            perror("waitpid");
            return reaped_main ? 1 : -1;
        }
        if (pid == 0)
            return reaped_main;             /* children left, none dead yet */
        if (pid == main_pid) {
            *main_status = status;
            reaped_main = 1;
        }
    }
}

/*
 * Pass a signal on to the command. Signals the tty driver generated (Ctrl+C,
 * si_code SI_KERNEL) are not forwarded: the command is in the same foreground
 * process group as the init, so it already received its own copy.
 */
static void forward_signal(pid_t main_pid, const siginfo_t *info)
{
    if (info->si_code == SI_KERNEL)
        return;
    if (kill(main_pid, info->si_signo) == -1 && errno != ESRCH)
        perror("kill(command)");
}

/*
 * Sleep until one of our signals is pending; forward it unless it is SIGCHLD.
 * The timeout is only a safety net: every loop round reaps first, so even a
 * lost SIGCHLD cannot leave a zombie for longer than WAKE_SECONDS.
 */
static void wait_for_event(const sigset_t *set, pid_t main_pid)
{
    struct timespec timeout = { WAKE_SECONDS, 0 };
    siginfo_t info;

    if (sigtimedwait(set, &info, &timeout) == -1) {
        if (errno != EAGAIN && errno != EINTR)
            perror("sigtimedwait");
        return;
    }
    if (info.si_signo != SIGCHLD)
        forward_signal(main_pid, &info);
}

int minit_run(char *const argv[])
{
    sigset_t set, old_mask;
    pid_t main_pid;
    int status = 0, done;

    if (prctl(PR_SET_NAME, INIT_NAME, 0, 0, 0) == -1)
        perror("prctl(PR_SET_NAME)");       /* only cosmetic: carry on */
    build_signal_set(&set);
    if (sigprocmask(SIG_BLOCK, &set, &old_mask) == -1) {
        perror("sigprocmask(block)");
        return EXIT_NOT_FOUND;
    }
    main_pid = start_main_child(argv, &old_mask);
    if (main_pid == -1)
        return EXIT_NOT_FOUND;
    for (;;) {
        done = reap_children(main_pid, &status);
        if (done == 1)
            return exit_code_from_status(status);
        if (done == -1) {
            fprintf(stderr, "minibox: init: lost track of %s\n", argv[0]);
            return EXIT_NOT_FOUND;
        }
        wait_for_event(&set, main_pid);
    }
}
