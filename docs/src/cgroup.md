# src/cgroup.c

## Purpose
Resource limits with cgroups v2: check that cgroup v2 is usable, create one leaf cgroup per container,
write the memory/CPU/pids limits, move the container into it, read usage statistics, and remove it.
Only plain `open`/`read`/`write`/`close`/`mkdir`/`rmdir` on `/sys/fs/cgroup` — no external tools.

## Hierarchy used
```
/sys/fs/cgroup                 root: cgroup.subtree_control gets +memory +cpu +pids
└── minibox/                   created once, kept; NO processes; subtree_control +memory +cpu +pids
    └── box-<container id>/    one per container: limits + the container's processes
```
Why: (1) a cgroup can only use a controller its **parent** enabled in `cgroup.subtree_control`, so controllers are
enabled top-down; (2) the **no-internal-processes** rule forbids processes in a non-root cgroup that enables
controllers for children, so processes only go into the `box-*` leaves. `minibox/` is never removed, because a
concurrent container could be creating its leaf at that moment.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int cgroup_limits_set(const struct cgroup_limits *lim)` | limits | 1 if any of memory/cpu/pids is set | – |
| `int cgroup_check_v2(void)` | – | 0 / -1 | `statfs(/sys/fs/cgroup)` must report `CGROUP2_SUPER_MAGIC`; `cgroup.controllers` must list `memory`, `cpu`, `pids`; clear message otherwise |
| `int cgroup_setup(struct cgroup *cg, const char *id, pid_t pid, const struct cgroup_limits *lim)` | leaf to fill, container ID, container host PID, limits | 0 / -1 | `prepare_base`, `mkdir box-<id>` (sets `cg->created`), `apply_limits`, `add_process`. On -1 with `cg->created` set, the caller must call `cgroup_remove`. |
| `void cgroup_print_stats(const struct cgroup *cg)` | leaf | – | prints `[stats]` lines to stderr: `memory.peak`, `cpu.usage_usec` (from `cpu.stat`), `pids.peak`, `oom_kill` (from `memory.events`); skips missing files |
| `long long cgroup_oom_kills(const struct cgroup *cg)` | leaf | `oom_kill` count, 0 if unknown | – |
| `void cgroup_remove(struct cgroup *cg)` | leaf | – | `rmdir`, retried every 100 ms for up to 1 s while `EBUSY` (processes still exiting); on failure prints the `sudo rmdir` command |
| `void cgroup_remove_leftover(const char *id)` | container ID | – | for `minibox rm` of a dead container: `rmdir box-<id>` via `cgroup_remove`; silent if the leaf does not exist (the container had no limits) |
| `int cgroup_join_leaf(const char *id, pid_t pid)` | container ID, PID to move | 0 moved / 1 no cgroup / -1 | for `minibox exec`: `access(box-<id>)` (`ENOENT` → 1, the container has no limits), then `add_process` writes the PID to `cgroup.procs`. Must be called while the caller still sees the host's `/sys/fs/cgroup`. |
| static `join`, `write_file`, `read_file`, `write_in`, `file_exists`, `has_word` | – | 0/-1 or bool | file helpers; `write_file` reports path + value + `strerror` because the kernel rejects bad values in `write()` |
| static `enable_controllers(dir)` | cgroup dir | 0 / -1 | reads `cgroup.subtree_control`, writes `+memory`, `+cpu`, `+pids` only for missing ones |
| static `prepare_base()` | – | 0 / -1 | enable on root → `mkdir minibox` (EEXIST ok) → enable on `minibox/` |
| static `write_memory_limit(leaf, bytes)` | – | 0 / -1 | `memory.max = bytes`; `memory.swap.max = 0` if the file exists |
| static `write_cpu_limit(leaf, quota)` | – | 0 / -1 | `cpu.max = "<quota> 100000"` |
| static `write_pids_limit(leaf, n)` | – | 0 / -1 | `pids.max = n` |
| static `apply_limits(leaf, lim)` | – | 0 / -1 | writes only the limits that were given, then `memory.oom.group = 1` |
| static `add_process(leaf, pid)` | – | 0 / -1 | writes the PID to `cgroup.procs` (moves the process) |
| static `read_stat(leaf, file, key, &out)` | file, `NULL` or key | 0 / -1 | single-number files or `"key value"` lines (exact key match, so `oom_group_kill` ≠ `oom_kill`) |

Stats output format (stderr):
```
[stats] memory.peak    13107200 bytes (12.5 MiB)
[stats] cpu.usage_usec 1503000 (1.50 s)
[stats] pids.peak      3
[stats] oom_kill       0
```

## System calls used and why
- `statfs` – identify the filesystem at `/sys/fs/cgroup` as cgroup v2 by its magic number.
- `open`, `read`, `write`, `close` – every cgroup knob is a file; writing a value configures the kernel.
- `mkdir` – creating a directory in cgroupfs creates a cgroup (the kernel fills in its files).
- `rmdir` – removes an *empty* cgroup (`EBUSY` while it still has processes).
- `access` – optional files (`memory.swap.max`, `memory.peak`, `pids.peak`) are skipped if absent.
- `nanosleep` – delay between `rmdir` retries.

## OS concept
Control groups: hierarchical resource accounting and limits enforced by the kernel scheduler (cpu),
memory manager/OOM killer (memory) and `fork()` (pids).

## How to test
```sh
sudo tests/phase3_test.sh
sudo ./minibox run --rootfs rootfs --memory 64M --cpus 0.5 --pids 32 -- sleep 10 &
cat /sys/fs/cgroup/minibox/box-*/{memory.max,cpu.max,pids.max}   # while it runs
ls /sys/fs/cgroup/minibox                                       # after exit: no box-*
```
