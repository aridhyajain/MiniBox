# include/rootfs.h

## Purpose
Public interface of the root-filesystem module (used by `src/container.c`, implemented in `src/rootfs.c`).

## Declarations
- `int rootfs_setup(const char *rootfs)`
  - Input: absolute path of the directory that becomes `/`. Must be called inside a new mount namespace (and a new PID namespace for `/proc` to be correct).
  - Return: 0 on success, -1 on error (already reported with `perror`).
  - Side effects: private mount propagation, bind mount, tmpfs on `/dev` and `/tmp`, procfs on `/proc`, root swapped via `pivot_root`, old root unmounted.

## System calls used
None (declarations only). See `docs/src/rootfs.md`.

## OS concept
Mount namespaces and `pivot_root`.

## How to test
`make`, then `sudo tests/phase1_test.sh`.
