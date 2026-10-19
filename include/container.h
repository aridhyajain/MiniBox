/* container.h - create and wait for an isolated container process. */
#ifndef MINIBOX_CONTAINER_H
#define MINIBOX_CONTAINER_H

#include "cgroup.h"
#include "net.h"

struct container_config {
    const char *rootfs;        /* absolute path of the root filesystem directory */
    const char *hostname;      /* hostname seen inside the container (UTS ns) */
    enum net_mode net;         /* NET_NONE (loopback only) or NET_VETH */
    struct cgroup_limits limits; /* all 0 = no cgroup is created */
    int stats;                 /* 1 = print cgroup usage after exit */
    char *const *argv;         /* command to run, NULL-terminated, argv[0] = program */
};

/*
 * container_run - start the command in new PID, UTS, mount, IPC and network
 * namespaces (and its own cgroup if limits are set) and wait for it.
 *
 * Returns the container command's exit status (128+N if killed by signal N,
 * 137 if OOM-killed), or -1 if the container could not be created or its
 * cgroup/network setup failed. SIGINT/SIGTERM kill the container; cleanup
 * still runs.
 */
int container_run(const struct container_config *cfg);

#endif /* MINIBOX_CONTAINER_H */
