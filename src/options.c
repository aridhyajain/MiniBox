/*
 * options.c - parsing and validation of the options of `minibox run`.
 *
 * Usage: minibox run [-d] [--name NAME] [--rootfs DIR] [--hostname NAME]
 *                    [--net none|veth] [--memory N[K|M|G]] [--cpus X]
 *                    [--pids N] [--stats] -- COMMAND [ARGS...]
 *
 * The kernel interfaces want raw numbers (bytes for memory.max, quota/period
 * for cpu.max), people want "64M" and "0.5": this file does the translation
 * and refuses nonsense before anything is created.
 */
#define _GNU_SOURCE
#include "options.h"
#include "state.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MIN_MEMORY (1LL << 20)      /* 1 MiB: less cannot even start a shell */
#define MIN_CPUS 0.01               /* 1000 us quota: the kernel's minimum */
#define MAX_PIDS 4194304L           /* kernel PID_MAX_LIMIT */

/* Parse the --net value. Returns 0 on success, -1 if unknown. */
static int parse_net_mode(const char *value, enum net_mode *mode)
{
    if (strcmp(value, "none") == 0)
        *mode = NET_NONE;
    else if (strcmp(value, "veth") == 0)
        *mode = NET_VETH;
    else {
        fprintf(stderr, "minibox: invalid --net '%s' (use none or veth)\n", value);
        return -1;
    }
    return 0;
}

/* K/M/G (either case) -> multiplier; no suffix -> 1; anything else -> 0. */
static unsigned long long size_multiplier(const char *suffix)
{
    if (suffix[0] == '\0')
        return 1;
    if (suffix[1] != '\0')
        return 0;
    switch (suffix[0]) {
    case 'k': case 'K': return 1ULL << 10;
    case 'm': case 'M': return 1ULL << 20;
    case 'g': case 'G': return 1ULL << 30;
    default:            return 0;
    }
}

/* --memory N[K|M|G] -> bytes. Prints an error and returns -1 if invalid. */
static int parse_memory(const char *s, long long *bytes)
{
    unsigned long long n, mult;
    char *end;

    errno = 0;
    n = (s[0] >= '0' && s[0] <= '9') ? strtoull(s, &end, 10) : 0;
    mult = (s[0] >= '0' && s[0] <= '9') ? size_multiplier(end) : 0;
    if (errno == ERANGE || mult == 0 || n > (unsigned long long)LLONG_MAX / mult) {
        fprintf(stderr, "minibox: invalid --memory '%s' (use N, NK, NM or NG, e.g. 64M)\n", s);
        return -1;
    }
    if ((long long)(n * mult) < MIN_MEMORY) {
        fprintf(stderr, "minibox: --memory '%s' is too small (minimum 1M)\n", s);
        return -1;
    }
    *bytes = (long long)(n * mult);
    return 0;
}

/* --cpus X (cores, may be fractional) -> cpu.max quota per 100000 us period. */
static int parse_cpus(const char *s, long *quota_us)
{
    long online = sysconf(_SC_NPROCESSORS_ONLN);
    char *end;
    double cpus;

    if (online == -1) {
        perror("sysconf(_SC_NPROCESSORS_ONLN)");
        return -1;
    }
    errno = 0;
    cpus = strtod(s, &end);
    if (end == s || *end != '\0' || errno == ERANGE || !(cpus >= MIN_CPUS)) {
        fprintf(stderr, "minibox: invalid --cpus '%s' (use a number >= %.2f, e.g. 0.5 or 2)\n",
                s, MIN_CPUS);
        return -1;
    }
    if (cpus > (double)online) {
        fprintf(stderr, "minibox: --cpus %s is more than the %ld online CPUs\n", s, online);
        return -1;
    }
    *quota_us = (long)(cpus * CGROUP_CPU_PERIOD + 0.5);
    return 0;
}

/* --pids N -> pids.max. */
static int parse_pids(const char *s, long *pids)
{
    char *end;
    long n;

    errno = 0;
    n = (s[0] >= '0' && s[0] <= '9') ? strtol(s, &end, 10) : 0;
    if (n < 1 || n > MAX_PIDS || errno == ERANGE || *end != '\0') {
        fprintf(stderr, "minibox: invalid --pids '%s' (use an integer from 1 to %ld)\n",
                s, MAX_PIDS);
        return -1;
    }
    *pids = n;
    return 0;
}

/* --name NAME: must be a legal container name (uniqueness is checked at registration). */
static int parse_name(const char *value, const char **name)
{
    if (!state_valid_name(value)) {
        fprintf(stderr, "minibox: invalid --name '%s' (use letters, digits, _ . -; "
                "at most %d characters; not a 12-digit hex ID)\n", value, STATE_NAME_MAX);
        return -1;
    }
    *name = value;
    return 0;
}

/* Handle one "--option value" pair. Returns 0 or -1 (message printed). */
static int parse_option(const char *opt, const char *value,
                        struct container_config *cfg, const char **name)
{
    if (strcmp(opt, "--rootfs") == 0)
        cfg->rootfs = value;
    else if (strcmp(opt, "--hostname") == 0)
        cfg->hostname = value;
    else if (strcmp(opt, "--net") == 0)
        return parse_net_mode(value, &cfg->net);
    else if (strcmp(opt, "--memory") == 0)
        return parse_memory(value, &cfg->limits.memory_bytes);
    else if (strcmp(opt, "--cpus") == 0)
        return parse_cpus(value, &cfg->limits.cpu_quota_us);
    else if (strcmp(opt, "--pids") == 0)
        return parse_pids(value, &cfg->limits.pids_max);
    else if (strcmp(opt, "--name") == 0)
        return parse_name(value, name);
    else {
        fprintf(stderr, "minibox: unknown option '%s'\n", opt);
        return -1;
    }
    return 0;
}

/*
 * Fill opts from the arguments after "run" (argv[0] is "run"). Returns 0 on
 * success, -1 on a usage error. cfg.rootfs is still the user's (possibly
 * relative) path here.
 */
int options_parse_run(int argc, char **argv, struct run_options *opts)
{
    struct container_config *cfg = &opts->cfg;
    int i;

    memset(opts, 0, sizeof(*opts));     /* limits 0 = unset, stats off, foreground */
    cfg->rootfs = DEFAULT_ROOTFS;
    cfg->hostname = DEFAULT_HOSTNAME;
    cfg->net = NET_NONE;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            if (i + 1 >= argc)
                return -1;              /* "--" but no command */
            cfg->argv = &argv[i + 1];
            return 0;
        }
        if (strcmp(argv[i], "--stats") == 0) {
            cfg->stats = 1;             /* a flag: takes no value */
            continue;
        }
        if (strcmp(argv[i], "-d") == 0) {
            opts->detach = 1;           /* a flag too */
            continue;
        }
        if (i + 1 >= argc)
            return -1;                  /* option without a value */
        if (parse_option(argv[i], argv[i + 1], cfg, &opts->name) == -1)
            return -1;
        i++;
    }
    return -1;                          /* no "--" separator found */
}

/*
 * Resolve `path` to an absolute path in `resolved` and check that it looks
 * like an Alpine rootfs (directory containing bin/busybox).
 */
int options_validate_rootfs(const char *path, char *resolved)
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
