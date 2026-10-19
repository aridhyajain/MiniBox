# include/container.h

## Purpose
Public interface of the container module (used by `src/main.c`, implemented in `src/container.c`).

## Declarations
- `struct container_config { const char *rootfs; const char *hostname; enum net_mode net; char *const *argv; }`
  - `rootfs`: absolute path of the root filesystem directory.
  - `hostname`: hostname seen inside the UTS namespace.
  - `net`: `NET_NONE` (loopback only) or `NET_VETH` (loopback + veth link to the host); type from `net.h`.
  - `argv`: NULL-terminated command vector (`argv[0]` is executed with `execvp`).
- `int container_run(const struct container_config *cfg)`
  - Return: command's exit status (128+N if killed by signal N), or -1 if the container could not be created or its network setup failed.
  - Errors: printed by the implementation. Side effects: creates and reaps a child; in veth mode creates and removes a host-side interface.

## System calls used
None (declarations only). See `docs/src/container.md`.

## OS concept
Interface between the CLI layer and the namespace/process layer.

## How to test
`make`; behaviour via `sudo tests/phase1_test.sh` and `sudo tests/phase2_test.sh`.
