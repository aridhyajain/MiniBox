/* cgroup.h - resource limits with cgroups v2. */
#ifndef MINIBOX_CGROUP_H
#define MINIBOX_CGROUP_H

#include <sys/types.h>

#define CGROUP_ROOT        "/sys/fs/cgroup"
#define CGROUP_BASE        CGROUP_ROOT "/minibox"   /* parent of all containers */
#define CGROUP_CPU_PERIOD  100000                   /* cpu.max period, in us */
#define CGROUP_PATH_LEN    256

/* Requested limits; 0 means "not set". */
struct cgroup_limits {
    long long memory_bytes;   /* memory.max */
    long cpu_quota_us;        /* cpu.max quota per CGROUP_CPU_PERIOD */
    long pids_max;            /* pids.max */
};

/* One container's leaf cgroup: CGROUP_BASE/box-<container id>. */
struct cgroup {
    char path[CGROUP_PATH_LEN];
    int created;              /* 1 once the directory exists (needs removal) */
};

/* Returns 1 if any limit is set, else 0. No limits => no cgroup at all. */
int cgroup_limits_set(const struct cgroup_limits *lim);

/*
 * Check that cgroup v2 is mounted at /sys/fs/cgroup and that the memory, cpu
 * and pids controllers exist. Returns 0, or -1 after printing why not.
 */
int cgroup_check_v2(void);

/*
 * Create CGROUP_BASE (if needed) with controllers enabled, create the leaf
 * box-<id> and write the limits. No process is in it yet: the container is
 * either created inside it (clone3 + CLONE_INTO_CGROUP, see cgroup_open_dir)
 * or moved in later with cgroup_add_process.
 * Returns 0 or -1 (message printed). If cg->created is set afterwards the
 * caller must call cgroup_remove(), also on failure.
 */
int cgroup_create(struct cgroup *cg, const char *id,
                  const struct cgroup_limits *lim);

/*
 * Open the leaf directory (O_RDONLY|O_DIRECTORY|O_CLOEXEC) for clone3's
 * CLONE_INTO_CGROUP. Returns the descriptor, or -1 with a message printed.
 * The caller closes it.
 */
int cgroup_open_dir(const struct cgroup *cg);

/*
 * Move `pid` into the leaf by writing it to cgroup.procs (the slow path:
 * a cgroup migration). Returns 0 or -1 (message printed).
 */
int cgroup_add_process(const struct cgroup *cg, pid_t pid);

/* Print memory.peak, cpu.stat usage_usec, pids.peak, oom_kill to stderr. */
void cgroup_print_stats(const struct cgroup *cg);

/*
 * Move `pid` into the leaf box-<id> of an already running container (used by
 * `exec`). Returns 0 if moved, 1 if the container has no cgroup (it was
 * started without limits; nothing to do), -1 with a message on error.
 */
int cgroup_join_leaf(const char *id, pid_t pid);

/* Number of OOM kills recorded in memory.events (0 if unknown). */
long long cgroup_oom_kills(const struct cgroup *cg);

/* rmdir the leaf, retrying for up to 1 s while it is still busy. */
void cgroup_remove(struct cgroup *cg);

/*
 * Remove the leaf box-<id> left behind by a dead container. Nothing to do
 * (and no message) if it does not exist, e.g. the container had no limits.
 */
void cgroup_remove_leftover(const char *id);

#endif /* MINIBOX_CGROUP_H */
