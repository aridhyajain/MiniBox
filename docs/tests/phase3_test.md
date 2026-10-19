# tests/phase3_test.sh

## Purpose
End-to-end check of Phase 3 (cgroup v2 limits, `--stats`, cleanup). Run with sudo; it never calls sudo itself.
Needs `./minibox`, the Alpine rootfs, and host `timeout`. Inside the container only busybox applets are used
(`sh`, `sleep`, `seq`, `timeout`, `cat`). Takes about 20 seconds. Refuses to start if `box-*` leftovers exist
(prints the `rmdir` command).

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`, counts failures |
| `mb [options] -- cmd...` | runs `minibox run --rootfs rootfs ...` with stdin closed |
| `leaf_count` | number of `/sys/fs/cgroup/minibox/box-*` directories |
| `wait_for_leaf` | polls up to 5 s for a leaf whose `cgroup.procs` is non-empty; prints its path |
| `check_no_leftovers situation` | PASS if no `box-*` directory exists |

## Checks
1. While `--memory 64M --cpus 0.5 --pids 32 -- sleep 5` runs: `memory.max` = 67108864, `cpu.max` = `50000 100000`,
   `pids.max` = 32, `memory.swap.max` = 0; it exits 0; no leftovers.
2. `--memory 32M` + doubling string loop: exit 137, "OOM-killed" printed, no leftovers. (Host `timeout 60` is only a safety net.)
3. `--pids 20 --stats`: an inner `sh` starts up to 100 `sleep`s; the outer `sh` (PID 1) counts processes with
   `set -- /proc/[0-9]*; echo $#` (a glob, so it needs no fork). Count must be 2–20 and `pids.peak` ≤ 20.
4. `--cpus 0.5 --stats`: a 3-second busy loop; `cpu.usage_usec / wall time` must be 35–65 %.
5. `--memory 64M -- nosuchcmd`: exit 127, no leftovers.
6. SIGTERM to a running `minibox` (`--pids 32 -- sleep 30`): exit 137, no leftovers.
7. No limit flags: works, `/proc/self/cgroup` does not contain `/minibox/`, no `box-*` created;
   with `--pids 32` it does show `/minibox/box-<id>` (no cgroup namespace, so the host path is visible).

## Notes on the test design
- **`timeout` must not be PID 1.** Busybox `timeout` execs the command in its own process and sends it SIGTERM.
  PID 1 of a namespace ignores signals from inside the namespace unless it installed a handler, so
  `minibox run -- timeout 3 sh -c 'while :; do :; done'` would never stop. The test runs
  `sh -c 'timeout 3 sh -c "..."; true'` so the loop is PID 2+.
- **Fork failures abort `ash`.** When `&` cannot fork, busybox `ash` may exit with "can't fork", so the
  counting is done by a different shell than the one that forks.
- **CPU % is noisy on WSL2**, hence the wide band; wall time includes ~0.1 s of container setup.

## System calls used and why
None directly; exercises `cgroup.c` (mkdir/write/rmdir on cgroupfs) and the signal handling in `container.c`.

## OS concept
Verifying cgroup enforcement: OOM killer (memory), CFS bandwidth control (cpu), fork limit (pids).

## How to test
```sh
make && sudo tests/phase3_test.sh
```
