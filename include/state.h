/* state.h - per-container state kept on disk so separate minibox commands can find it. */
#ifndef MINIBOX_STATE_H
#define MINIBOX_STATE_H

#include "net.h"

#include <limits.h>
#include <stddef.h>
#include <sys/types.h>

#define STATE_DEFAULT_ROOT "/var/lib/minibox/containers"
#define STATE_ENV_VAR      "MINIBOX_STATE_DIR"
#define STATE_ID_LEN       12     /* lowercase hex characters */
#define STATE_NAME_MAX     63
#define STATE_CMD_MAX      1024
#define STATE_HOST_MAX     65     /* HOST_NAME_MAX + NUL */

#define STATUS_RUNNING "running"
#define STATUS_EXITED  "exited"
#define STATUS_DEAD    "dead"

struct container_config;

/* Everything stored in <state_root>/<id>/state. 0 / "" means "not set". */
struct container_state {
    char id[STATE_ID_LEN + 1];
    char name[STATE_NAME_MAX + 1];
    char status[16];                /* STATUS_RUNNING or STATUS_EXITED as stored */
    long pid;                       /* init of the container, host PID (0 = not started) */
    long monitor_pid;               /* process that waits for it and records the exit */
    int exit_code;
    long long created_at;           /* epoch seconds */
    long long started_at;
    long long finished_at;
    char command[STATE_CMD_MAX];
    char rootfs[PATH_MAX];
    char hostname[STATE_HOST_MAX];
    char net[8];                    /* "none" or "veth" */
    long long memory;               /* bytes */
    double cpus;                    /* cores */
    long pids;
    int init;                       /* 1 = started with --init (PID 1 is minibox-init) */
    char ip[NET_ADDR_LEN];          /* container address, veth only */
};

/* State directory: $MINIBOX_STATE_DIR if set, else STATE_DEFAULT_ROOT. */
const char *state_root(void);

/* 1 if `name` is a legal container name, else 0. */
int state_valid_name(const char *name);

/* Fill `st` from the run configuration (command, limits, net, rootfs...). */
void state_init_from_config(struct container_state *st,
                            const struct container_config *cfg, const char *name);

/*
 * Register a new container: under a lock, check that st->name is unused,
 * pick a free 12-hex ID (creates <root>/<id>/, mode 0700), pick the lowest
 * free subnet if st->net is "veth" (sets st->ip), set status=running,
 * created_at and monitor_pid=getpid(), and write the state file.
 * Returns 0, or -1 with a message printed.
 */
int state_create(struct container_state *st);

/* Write <root>/<id>/state atomically (temp file + rename). 0 / -1 (message). */
int state_save(const struct container_state *st);

/* Read the state of container `id`. 0 / -1 (errno set, no message). */
int state_load(const char *id, struct container_state *st);

/*
 * All containers, oldest first, in a malloc'd array (free() it). A missing
 * state directory is an empty list. Returns 0, or -1 with a message.
 */
int state_list(struct container_state **out, size_t *count);

/* Find a container by exact ID or exact name. 0, or -1 with a message. */
int state_find(const char *key, struct container_state *st);

/* Delete <root>/<id>/ and its files. 0 / -1 (message). */
int state_remove(const char *id);

/* Build <root>/<id>/<file> into buf. 0 / -1 if it does not fit. */
int state_path(const char *id, const char *file, char *buf, size_t size);

/*
 * Copy everything currently readable from in_fd to out_fd with read()/write()
 * (no stdio buffering, so output appears immediately). 0 / -1 (message).
 */
int state_copy_fd(int in_fd, int out_fd);

/* 1 if the process exists and is not a zombie. */
int process_alive(pid_t pid);

/* 1 if `pid` is the init (PID 1) of a PID namespace, judged from /proc/<pid>/status. */
int state_pid_is_ns_init(pid_t pid);

/* Stored status, except running with a vanished monitor is reported as dead. */
const char *state_effective_status(const struct container_state *st);

/* Subnet index n from "10.0.n.2", or -1 if there is no address. */
int state_net_index(const struct container_state *st);

#endif /* MINIBOX_STATE_H */
