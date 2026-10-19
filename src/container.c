/*
 * container.c - create the container process with clone() and wait for it.
 *
 * OS concept: clone() is fork() with flags. Each CLONE_NEW* flag gives the
 * child a private copy of one kernel resource view (namespace).
 *
 * Parent/child handshake: some setup needs the child's host PID and must
 * finish before the child runs its command: moving it into its cgroup (so
 * limits apply from the first instruction) and, with --net veth, moving one
 * veth end into its network namespace. Two pipes synchronise them:
 *     ready: child -> parent  'R'  "my own setup is done"
 *     go:    parent -> child  'G'  "cgroup + network ready, exec now"
 *                             'A'  "setup failed, give up"
 * A pipe also tells each side if the other died: reading 0 bytes (EOF) means
 * every write end was closed.
 *
 * SIGINT/SIGTERM sent to minibox kill the container (SIGKILL to its PID 1,
 * which takes the whole PID namespace with it); cleanup then runs normally.
 */
#define _GNU_SOURCE
#include "container.h"
#include "cgroup.h"
#include "net.h"
#include "rootfs.h"

#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#define STACK_SIZE (1024 * 1024)
#define DEFAULT_PATH "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

#define MSG_READY 'R'
#define MSG_GO    'G'
#define MSG_ABORT 'A'

struct sync_pipes {
    int ready[2];   /* [0] parent reads, [1] child writes */
    int go[2];      /* [0] child reads,  [1] parent writes */
};

/* Everything the child needs; passed through clone()'s void * argument. */
struct child_args {
    const struct container_config *cfg;
    struct sync_pipes *pipes;
};

struct saved_signals {
    struct sigaction int_act;
    struct sigaction term_act;
};

/*
 * The only global state in minibox. A signal handler receives no arguments,
 * so it can only communicate through globals; volatile sig_atomic_t is the
 * type C guarantees can be written safely from a handler.
 */
static volatile sig_atomic_t stop_signal;   /* SIGINT/SIGTERM received, or 0 */
static volatile sig_atomic_t target_pid;    /* container to kill, or 0 */

/*
 * clone() does not allocate a stack for the child: we must. Stacks grow
 * downward on x86, so the child is given the HIGH end of the region.
 * Returns the mapping base (for munmap) and stores the top in *top.
 */
static void *alloc_stack(void **top)
{
    void *base = mmap(NULL, STACK_SIZE, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);

    if (base == MAP_FAILED) {
        perror("mmap(child stack)");
        return NULL;
    }
    *top = (char *)base + STACK_SIZE;
    return base;
}

/* ---------------------------- sync pipes ---------------------------- */

static void close_fd(int *fd)
{
    if (*fd != -1) {
        close(*fd);
        *fd = -1;
    }
}

static void close_pipes(struct sync_pipes *p)
{
    close_fd(&p->ready[0]);
    close_fd(&p->ready[1]);
    close_fd(&p->go[0]);
    close_fd(&p->go[1]);
}

/* O_CLOEXEC: the descriptors vanish automatically when the child execs. */
static int make_sync_pipes(struct sync_pipes *p)
{
    p->ready[0] = p->ready[1] = p->go[0] = p->go[1] = -1;
    if (pipe2(p->ready, O_CLOEXEC) == -1) {
        perror("pipe2(ready)");
        return -1;
    }
    if (pipe2(p->go, O_CLOEXEC) == -1) {
        perror("pipe2(go)");
        close_pipes(p);
        return -1;
    }
    return 0;
}

/*
 * Read one byte, retrying on EINTR unless a stop signal arrived.
 * Returns 1, 0 (EOF) or -1.
 */
static int read_byte(int fd, char *c)
{
    ssize_t n;

    do {
        n = read(fd, c, 1);
    } while (n == -1 && errno == EINTR && !stop_signal);
    if (n == -1 && errno != EINTR)
        perror("read(sync pipe)");
    return (int)n;
}

/* ------------------------------ signals ------------------------------ */

/* kill() is async-signal-safe, so the handler can stop the container itself. */
static void on_stop_signal(int sig)
{
    int saved_errno = errno;

    stop_signal = sig;
    if (target_pid > 0)
        kill((pid_t)target_pid, SIGKILL);
    errno = saved_errno;
}

/*
 * No SA_RESTART: a blocking read()/waitpid() returns EINTR so the parent
 * notices the signal. exec() resets the handlers in the child.
 */
static int install_stop_handlers(struct saved_signals *old)
{
    struct sigaction sa;

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_stop_signal;
    sigemptyset(&sa.sa_mask);
    stop_signal = 0;
    target_pid = 0;
    if (sigaction(SIGINT, &sa, &old->int_act) == -1) {
        perror("sigaction(SIGINT)");
        return -1;
    }
    if (sigaction(SIGTERM, &sa, &old->term_act) == -1) {
        perror("sigaction(SIGTERM)");
        sigaction(SIGINT, &old->int_act, NULL);
        return -1;
    }
    return 0;
}

static void restore_handlers(const struct saved_signals *old)
{
    target_pid = 0;
    if (sigaction(SIGINT, &old->int_act, NULL) == -1)
        perror("sigaction(restore SIGINT)");
    if (sigaction(SIGTERM, &old->term_act, NULL) == -1)
        perror("sigaction(restore SIGTERM)");
}

static int write_byte(int fd, char c)
{
    ssize_t n;

    do {
        n = write(fd, &c, 1);
    } while (n == -1 && errno == EINTR);
    if (n != 1) {
        perror("write(sync pipe)");
        return -1;
    }
    return 0;
}

/* ------------------------------- child ------------------------------- */

/* Set the UTS-namespace hostname (changes only this container's view). */
static int set_hostname(const char *name)
{
    if (sethostname(name, strlen(name)) == -1) {
        perror("sethostname");
        return -1;
    }
    return 0;
}

/* Tell the parent we are ready, then block until it says go (or abort). */
static int child_handshake(struct sync_pipes *p)
{
    char c;
    int n;

    if (write_byte(p->ready[1], MSG_READY) == -1)
        return -1;
    n = read_byte(p->go[0], &c);
    if (n == -1)
        return -1;
    if (n == 0 || c != MSG_GO) {
        fprintf(stderr, "minibox: container setup aborted by parent\n");
        return -1;
    }
    return 0;
}

/* Everything that must happen before the user's program starts. */
static int child_setup(const struct child_args *args)
{
    const struct container_config *cfg = args->cfg;

    /* close the parent's ends so EOF is seen if the parent dies */
    close_fd(&args->pipes->ready[0]);
    close_fd(&args->pipes->go[1]);

    if (set_hostname(cfg->hostname) == -1)
        return -1;
    if (net_loopback_up() == -1)
        return -1;
    if (rootfs_setup(cfg->rootfs) == -1)
        return -1;
    if (child_handshake(args->pipes) == -1)
        return -1;
    if (cfg->net == NET_VETH &&
        net_set_ipv4(NET_CONTAINER_IF, NET_CONTAINER_ADDR, NET_PREFIX) == -1)
        return -1;
    if (chdir("/") == -1) {
        perror("chdir(/)");
        return -1;
    }
    if (setenv("PATH", DEFAULT_PATH, 1) == -1 || setenv("HOME", "/root", 1) == -1) {
        perror("setenv");
        return -1;
    }
    return 0;
}

/*
 * Runs as the first process (PID 1) of the new PID namespace.
 * Setup errors exit 1; a failed exec exits 127 (shell convention).
 */
static int child_main(void *arg)
{
    const struct child_args *args = arg;
    char *const *argv = args->cfg->argv;

    if (child_setup(args) == -1)
        return 1;
    execvp(argv[0], argv);
    /* only reached if exec failed */
    fprintf(stderr, "exec %s: %s\n", argv[0], strerror(errno));
    return 127;
}

/* ------------------------------- parent ------------------------------- */

/*
 * Wait for the child's 'R' (all network modes). Returns 0 when the child is
 * ready, -1 on EOF/error, which means it died before the handshake.
 */
static int parent_wait_ready(int fd)
{
    char c;

    return (read_byte(fd, &c) == 1 && c == MSG_READY) ? 0 : -1;
}

/* Network setup that needs the live child; nothing to do for --net none. */
static int parent_network_setup(const struct container_config *cfg, pid_t pid)
{
    if (cfg->net != NET_VETH)
        return 0;
    return net_veth_setup(pid);
}

/* Wait for the child; convert its wait status to a shell-style exit code. */
static int wait_for_child(pid_t pid)
{
    int status;

    while (waitpid(pid, &status, 0) == -1) {
        if (errno != EINTR) {
            perror("waitpid");
            return -1;
        }
    }
    if (WIFEXITED(status))
        return WEXITSTATUS(status);
    if (WIFSIGNALED(status))
        return 128 + WTERMSIG(status);
    return -1;
}

/*
 * Tell the child to go (setup_ok) or abort. The parent keeps go[0] open
 * until after this write so the write can never hit a closed pipe (SIGPIPE).
 */
static int release_child(struct sync_pipes *p, int setup_ok)
{
    if (write_byte(p->go[1], setup_ok ? MSG_GO : MSG_ABORT) == -1)
        return -1;
    return setup_ok ? 0 : -1;
}

/*
 * Everything the parent does between clone() and releasing the child:
 * wait for 'R', put the child in its cgroup, set up veth.
 * *child_died is set if the child exited before saying 'R'.
 */
static int parent_setup(const struct container_config *cfg, pid_t pid,
                        struct sync_pipes *p, struct cgroup *cg, int *child_died)
{
    if (parent_wait_ready(p->ready[0]) == -1) {
        *child_died = !stop_signal;
        return -1;
    }
    if (cgroup_limits_set(&cfg->limits) &&
        cgroup_setup(cg, pid, &cfg->limits) == -1) {
        fprintf(stderr, "minibox: cgroup setup failed; container stopped\n");
        return -1;
    }
    if (parent_network_setup(cfg, pid) == -1)
        return -1;
    return stop_signal ? -1 : 0;
}

/* Setup failed but the child may still be blocked on the go pipe: kill it. */
static void kill_child(pid_t pid)
{
    if (kill(pid, SIGKILL) == -1 && errno != ESRCH)
        perror("kill(container)");
}

/* After the container exited: stats, OOM check, remove the leaf cgroup. */
static int finish_cgroup(const struct container_config *cfg, struct cgroup *cg,
                         int result)
{
    if (!cg->created)
        return result;
    if (cfg->stats)
        cgroup_print_stats(cg);
    if (cgroup_oom_kills(cg) > 0) {
        fprintf(stderr, "minibox: container was OOM-killed (memory limit %lld bytes)\n",
                cfg->limits.memory_bytes);
        result = 137;               /* 128 + SIGKILL, like a shell reports it */
    }
    cgroup_remove(cg);
    return result;
}

/* Explain why the container ended early, if it did. */
static void report_early_exit(int child_died, int result)
{
    if (child_died)
        fprintf(stderr, "minibox: container died during setup (exit status %d)\n",
                result);
    if (stop_signal)
        fprintf(stderr, "minibox: received %s, container killed\n",
                strsignal(stop_signal));
}

/*
 * Parent side after clone(): setup, release, wait, clean up.
 * Cleanup (veth, cgroup) runs on every path.
 */
static int supervise(const struct container_config *cfg, pid_t pid,
                     struct sync_pipes *pipes)
{
    struct cgroup cg = { "", 0 };
    int result, setup_failed, child_died = 0;

    close_fd(&pipes->ready[1]);   /* so EOF on ready[0] means "child gone" */
    setup_failed = parent_setup(cfg, pid, pipes, &cg, &child_died) == -1;
    if (release_child(pipes, !setup_failed) == -1)
        setup_failed = 1;
    close_pipes(pipes);
    if (setup_failed && !child_died)
        kill_child(pid);

    result = wait_for_child(pid);
    target_pid = 0;               /* reaped: the PID may be reused now */
    report_early_exit(child_died, result);
    if (cfg->net == NET_VETH)
        net_veth_cleanup(pid);    /* also covers a failed setup */
    result = finish_cgroup(cfg, &cg, result);
    return setup_failed ? -1 : result;
}

int container_run(const struct container_config *cfg)
{
    struct sync_pipes pipes;
    struct child_args args = { cfg, &pipes };
    struct saved_signals old_signals;
    void *stack_top;
    void *stack_base;
    pid_t pid;
    int result = -1;

    /* fail before creating anything if limits were asked for but impossible */
    if (cgroup_limits_set(&cfg->limits) && cgroup_check_v2() == -1)
        return -1;
    if (make_sync_pipes(&pipes) == -1)
        return -1;
    stack_base = alloc_stack(&stack_top);
    if (stack_base == NULL) {
        close_pipes(&pipes);
        return -1;
    }
    if (install_stop_handlers(&old_signals) == 0) {
        /* SIGCHLD makes the child's termination signal the parent like fork(). */
        pid = clone(child_main, stack_top,
                    CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWNS |
                    CLONE_NEWIPC | CLONE_NEWNET | SIGCHLD,
                    &args);
        if (pid == -1) {
            perror("clone");
        } else {
            target_pid = pid;
            result = supervise(cfg, pid, &pipes);
        }
        restore_handlers(&old_signals);
    }
    close_pipes(&pipes);
    if (munmap(stack_base, STACK_SIZE) == -1)
        perror("munmap(child stack)");
    return result;
}
