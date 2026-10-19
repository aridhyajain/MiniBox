# include/container.h

## Purpose
Public interface of the container module (used by `src/monitor.c`, implemented in `src/container.c`).

## Declarations
- `struct container_config`
  - `const char *rootfs` – absolute path of the root filesystem directory.
  - `const char *hostname` – hostname seen inside the UTS namespace.
  - `enum net_mode net` – `NET_NONE` (loopback only) or `NET_VETH` (from `net.h`).
  - `struct cgroup_limits limits` – memory/cpu/pids limits (from `cgroup.h`); all 0 = no cgroup is created.
  - `int stats` – 1 = print cgroup usage after the container exits.
  - `char *const *argv` – NULL-terminated command vector.
  - `const char *id` – container ID; the cgroup leaf is `box-<id>`.
  - `int net_index` – veth subnet `10.0.<n>.0/24` (veth only).
  - `void (*on_started)(pid_t pid, void *arg)`, `void *on_started_arg` – optional hook, called once when the container has been released to run.
- `int container_run(const struct container_config *cfg)`
  - Return: command's exit status (128+N if killed by signal N, 137 if OOM-killed), or -1 if the container could
    not be created or its cgroup/network setup failed.
  - Side effects: creates and reaps a child; in veth mode creates/removes a host interface; with limits creates and
    removes `/sys/fs/cgroup/minibox/box-<id>`; calls `on_started` after the child was released; temporarily installs SIGINT/SIGTERM handlers that kill the container.

## System calls used
None (declarations only). See `docs/src/container.md`.

## OS concept
Interface between the CLI layer and the namespace/cgroup/process layer.

## How to test
`make`; behaviour via `sudo tests/phase1_test.sh`, `phase2_test.sh`, `phase3_test.sh`.
