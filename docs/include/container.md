# include/container.h

## Purpose
Public interface of the container module (used by `src/main.c`, implemented in `src/container.c`).

## Declarations
- `struct container_config`
  - `const char *rootfs` – absolute path of the root filesystem directory.
  - `const char *hostname` – hostname seen inside the UTS namespace.
  - `enum net_mode net` – `NET_NONE` (loopback only) or `NET_VETH` (from `net.h`).
  - `struct cgroup_limits limits` – memory/cpu/pids limits (from `cgroup.h`); all 0 = no cgroup is created.
  - `int stats` – 1 = print cgroup usage after the container exits.
  - `char *const *argv` – NULL-terminated command vector.
- `int container_run(const struct container_config *cfg)`
  - Return: command's exit status (128+N if killed by signal N, 137 if OOM-killed), or -1 if the container could
    not be created or its cgroup/network setup failed.
  - Side effects: creates and reaps a child; in veth mode creates/removes a host interface; with limits creates and
    removes `/sys/fs/cgroup/minibox/box-<pid>`; temporarily installs SIGINT/SIGTERM handlers that kill the container.

## System calls used
None (declarations only). See `docs/src/container.md`.

## OS concept
Interface between the CLI layer and the namespace/cgroup/process layer.

## How to test
`make`; behaviour via `sudo tests/phase1_test.sh`, `phase2_test.sh`, `phase3_test.sh`.
