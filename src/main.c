/*
 * main.c - command-line parsing for minibox.
 *
 * Usage: minibox run [--rootfs DIR] [--hostname NAME] -- COMMAND [ARGS...]
 */
#define _GNU_SOURCE
#include "container.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define DEFAULT_ROOTFS "rootfs"
#define DEFAULT_HOSTNAME "minibox"

static void usage(const char *prog)
{
    fprintf(stderr,
            "Usage: %s run [--rootfs DIR] [--hostname NAME] -- COMMAND [ARGS...]\n"
            "  --rootfs DIR     root filesystem directory (default: %s)\n"
            "  --hostname NAME  container hostname (default: %s)\n"
            "Must be run as root (sudo).\n",
            prog, DEFAULT_ROOTFS, DEFAULT_HOSTNAME);
}

/*
 * Fill cfg from argv. Returns 0 on success, -1 on a usage error.
 * cfg->rootfs is still the user's (possibly relative) path here.
 */
static int parse_args(int argc, char **argv, struct container_config *cfg)
{
    int i;

    cfg->rootfs = DEFAULT_ROOTFS;
    cfg->hostname = DEFAULT_HOSTNAME;
    cfg->argv = NULL;

    if (argc < 2 || strcmp(argv[1], "run") != 0)
        return -1;
    for (i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            if (i + 1 >= argc)
                return -1;              /* "--" but no command */
            cfg->argv = &argv[i + 1];
            return 0;
        }
        if (i + 1 >= argc)
            return -1;                  /* option without a value */
        if (strcmp(argv[i], "--rootfs") == 0)
            cfg->rootfs = argv[++i];
        else if (strcmp(argv[i], "--hostname") == 0)
            cfg->hostname = argv[++i];
        else
            return -1;
    }
    return -1;                          /* no "--" separator found */
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

/*
 * Resolve `path` to an absolute path in `resolved` and check that it looks
 * like an Alpine rootfs (directory containing bin/busybox).
 */
static int validate_rootfs(const char *path, char *resolved)
{
    char busybox[PATH_MAX];
    struct stat st;

    if (realpath(path, resolved) == NULL) {
        perror(path);
        return -1;
    }
    if (stat(resolved, &st) == -1) {
        perror(resolved);
        return -1;
    }
    if (!S_ISDIR(st.st_mode)) {
        fprintf(stderr, "minibox: %s is not a directory\n", resolved);
        return -1;
    }
    if (snprintf(busybox, sizeof(busybox), "%s/bin/busybox", resolved)
            >= (int)sizeof(busybox)) {
        fprintf(stderr, "minibox: rootfs path too long\n");
        return -1;
    }
    if (stat(busybox, &st) == -1) {
        perror(busybox);
        fprintf(stderr, "minibox: not an Alpine rootfs (run 'make rootfs' first)\n");
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct container_config cfg;
    char rootfs_abs[PATH_MAX];
    int status;

    if (parse_args(argc, argv, &cfg) == -1) {
        usage(argv[0]);
        return 1;
    }
    if (require_root() == -1)
        return 1;
    if (validate_rootfs(cfg.rootfs, rootfs_abs) == -1)
        return 1;
    cfg.rootfs = rootfs_abs;

    status = container_run(&cfg);
    return status == -1 ? 1 : status;
}
