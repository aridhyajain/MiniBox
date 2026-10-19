# include/cgroup.h

## Purpose
Public interface of the cgroup module (used by `src/container.c` and `src/main.c`, implemented in `src/cgroup.c`).

## Declarations
- Constants: `CGROUP_ROOT` (`/sys/fs/cgroup`), `CGROUP_BASE` (`/sys/fs/cgroup/minibox`),
  `CGROUP_CPU_PERIOD` (100000 µs), `CGROUP_PATH_LEN` (256).
- `struct cgroup_limits { long long memory_bytes; long cpu_quota_us; long pids_max; }` – 0 means "not set".
  `cpu_quota_us` is per `CGROUP_CPU_PERIOD`, so `--cpus 0.5` → 50000.
- `struct cgroup { char path[CGROUP_PATH_LEN]; int created; }` – one container's leaf; `created` = must be removed.
- `int cgroup_limits_set(lim)`, `int cgroup_check_v2(void)`, `int cgroup_setup(cg, pid, lim)`,
  `void cgroup_print_stats(cg)`, `long long cgroup_oom_kills(cg)`, `void cgroup_remove(cg)`.

Signatures, return values, errors and side effects: `docs/src/cgroup.md`.

## System calls used
None (declarations only).

## OS concept
cgroups v2 resource control.

## How to test
`make`, then `sudo tests/phase3_test.sh`.
