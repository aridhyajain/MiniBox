# include/container.h

## Purpose
Public interface of the container module (used by `src/main.c`, implemented in `src/container.c`).

## Declarations
- `struct container_config { const char *rootfs; const char *hostname; char *const *argv; }`
  - `rootfs`: absolute path of the root filesystem directory.
  - `hostname`: hostname seen inside the UTS namespace.
  - `argv`: NULL-terminated command vector (`argv[0]` is executed with `execvp`).
- `int container_run(const struct container_config *cfg)`
  - Input: filled config. Return: command's exit status (128+N if killed by signal N), or -1 if the container could not be created.
  - Errors: printed by the implementation with `perror`. Side effects: creates and reaps a child process.

## System calls used
None (declarations only). See `docs/src/container.md`.

## OS concept
Interface between the CLI layer and the namespace/process layer.

## How to test
Compiling `src/main.c` and `src/container.c` against it: `make`. Behaviour: `sudo tests/phase1_test.sh`.
