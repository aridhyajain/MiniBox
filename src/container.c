/*
 * container.c - create the container process with clone() and wait for it.
 *
 * OS concept: clone() is fork() with flags. Each CLONE_NEW* flag gives the
 * child a private copy of one kernel resource view (namespace).
 *
 * Parent/child handshake: with --net veth the parent must create the veth
 * pair and move one end into the child's network namespace, which it can only
 * do once the child exists. The child must not exec until that is finished,
 * so two pipes synchronise them:
 *     ready: child -> parent  'R'  "my own setup is done"
 *     go:    parent -> child  'G'  "network is ready, exec now"
 *                             'A'  "setup failed, give up"
 * A pipe also tells each side if the other died: reading 0 bytes (EOF) means
 * every write end was closed.
 */
#define _GNU_SOURCE
#include "container.h"
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

/* Read one byte, retrying on EINTR. Returns 1, 0 (EOF) or -1. */
static int read_byte(int fd, char *c)
{
    ssize_t n;

    do {
        n = read(fd, c, 1);
    } while (n == -1 && errno == EINTR);
    if (n == -1)
        perror("read(sync pipe)");
    return (int)n;
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

int container_run(const struct container_config *cfg)
{
    struct sync_pipes pipes;
    struct child_args args = { cfg, &pipes };
    void *stack_top;
    void *stack_base;
    pid_t pid;
    int result, setup_failed, child_died;

    if (make_sync_pipes(&pipes) == -1)
        return -1;
    stack_base = alloc_stack(&stack_top);
    if (stack_base == NULL) {
        close_pipes(&pipes);
        return -1;
    }
    /* SIGCHLD makes the child's termination signal the parent like fork(). */
    pid = clone(child_main, stack_top,
                CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWNS |
                CLONE_NEWIPC | CLONE_NEWNET | SIGCHLD,
                &args);
    if (pid == -1) {
        perror("clone");
        close_pipes(&pipes);
        munmap(stack_base, STACK_SIZE);
        return -1;
    }
    close_fd(&pipes.ready[1]);    /* so EOF on ready[0] means "child gone" */

    /* Same protocol in every mode: read 'R', set up network, send go/abort. */
    child_died = parent_wait_ready(pipes.ready[0]) == -1;
    setup_failed = child_died || parent_network_setup(cfg, pid) == -1;
    if (release_child(&pipes, !setup_failed) == -1)
        setup_failed = 1;
    close_pipes(&pipes);

    result = wait_for_child(pid);
    if (child_died)
        fprintf(stderr, "minibox: container died during setup (exit status %d)\n",
                result);
    if (cfg->net == NET_VETH)
        net_veth_cleanup(pid);     /* also covers a failed setup */
    if (munmap(stack_base, STACK_SIZE) == -1)
        perror("munmap(child stack)");
    return setup_failed ? -1 : result;
}
