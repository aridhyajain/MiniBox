/* options.h - parse and validate the options of `minibox run`. */
#ifndef MINIBOX_OPTIONS_H
#define MINIBOX_OPTIONS_H

#include "container.h"

#include <limits.h>

#define DEFAULT_ROOTFS   "rootfs"
#define DEFAULT_HOSTNAME "minibox"

struct run_options {
    struct container_config cfg;
    int detach;                 /* -d: run in the background */
    const char *name;           /* --name, or NULL */
};

/*
 * Parse the arguments of `run`; argv[0] is "run", the command follows "--".
 * Returns 0, or -1 on a usage error (a specific message is printed for an
 * invalid value; nothing for a structural error, the caller prints usage).
 * Pointers inside opts refer to argv, which must outlive opts.
 */
int options_parse_run(int argc, char **argv, struct run_options *opts);

/*
 * Resolve `path` to an absolute path in `resolved` (PATH_MAX bytes) and check
 * that it is an Alpine-style rootfs (a directory containing bin/busybox).
 * Returns 0, or -1 after printing why not.
 */
int options_validate_rootfs(const char *path, char *resolved);

#endif /* MINIBOX_OPTIONS_H */
