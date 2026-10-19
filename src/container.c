/*
 * container.c - create the container process with clone() and wait for it.
 *
 * OS concept: clone() is fork() with flags. Each CLONE_NEW* flag gives the
 * child a private copy of one kernel resource view (namespace).
 */
#define _GNU_SOURCE
#include "container.h"
#include "rootfs.h"

#include <errno.h>
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

/* Set the UTS-namespace hostname (changes only this container's view). */
static int set_hostname(const char *name)
{
    if (sethostname(name, strlen(name)) == -1) {
        perror("sethostname");
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
    const struct container_config *cfg = arg;

    if (set_hostname(cfg->hostname) == -1)
        return 1;
    if (rootfs_setup(cfg->rootfs) == -1)
        return 1;
    if (chdir("/") == -1) {
        perror("chdir(/)");
        return 1;
    }
    if (setenv("PATH", DEFAULT_PATH, 1) == -1 || setenv("HOME", "/root", 1) == -1) {
        perror("setenv");
        return 1;
    }
    execvp(cfg->argv[0], cfg->argv);
    /* only reached if exec failed */
    fprintf(stderr, "exec %s: %s\n", cfg->argv[0], strerror(errno));
    return 127;
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

int container_run(const struct container_config *cfg)
{
    void *stack_top;
    void *stack_base = alloc_stack(&stack_top);
    pid_t pid;
    int result;

    if (stack_base == NULL)
        return -1;
    /* SIGCHLD makes the child's termination signal the parent like fork(). */
    pid = clone(child_main, stack_top,
                CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWNS | SIGCHLD,
                (void *)cfg);
    if (pid == -1) {
        perror("clone");
        munmap(stack_base, STACK_SIZE);
        return -1;
    }
    result = wait_for_child(pid);
    if (munmap(stack_base, STACK_SIZE) == -1)
        perror("munmap(child stack)");
    return result;
}
