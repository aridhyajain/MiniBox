# tests/clone3_test.sh

## Purpose
End-to-end check of the start-up path with limits: the container is created **inside** its cgroup (`clone3` + `CLONE_INTO_CGROUP`),
and the old path (`clone()` + write to `cgroup.procs`, forced with the diagnostic switch `MINIBOX_NO_CLONE3=1`) still works and enforces
the same limits. Run with `sudo bash tests/clone3_test.sh`; it never calls sudo itself. Takes about a minute. Needs `./minibox`, the Alpine
rootfs and the host tools `ip`, `timeout`, `awk`, `sort`; `strace` is optional (those checks print `SKIP` without it). It sets
`MINIBOX_STATE_DIR` to a `mktemp -d` directory removed at the end (an `EXIT` trap also runs `rm -f` on every container still listed), and refuses to
start if `box-*` cgroups or `vethh*` interfaces already exist. Every minibox call goes through `timeout`, so a hang is a FAIL, not a stuck test.
It sources `tests/lib.sh` and is the last script in `tests/run_all.sh`. `INFO:` lines (the timing table) never fail the script.

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`, counts failures |
| `run_mode mode seconds cmd...` | `timeout` around a command; mode `clone3` runs with `MINIBOX_NO_CLONE3` unset, mode `migrate` with `MINIBOX_NO_CLONE3=1` |
| `mbr mode rootfs seconds [opts] -- cmd...` | foreground `minibox run`, stdin closed |
| `run_d mode [opts] -- cmd...` | detached run, prints the ID |
| `field id key`, `wait_running id s` | read / poll the state file |
| `wait_clean s`, `check_clean situation` | poll until no `box-*` cgroup and no `vethh*` interface exist |
| `veth_count`, `leaf_count`, `state_rows` | counters for leftovers (`ps -a` rows without the header) |
| `mode_checks mode` | the checks that must hold in both modes (below) |
| `time_us mode`, `stat_ms median\|min values...` | one timed run in microseconds; median / minimum in ms |

## Checks
- **a / b. both modes** (`mode_checks clone3`, then `mode_checks migrate`; names carry `[clone3]` / `[migrate]`):
  - detached `--memory 64M --cpus 0.5 --pids 32 -- sleep 30`: the init's host PID (state file) is a line of `box-<id>/cgroup.procs`; `memory.max` 67108864, `cpu.max` `50000 100000`, `pids.max` 32;
    no descriptor pointing into `/sys/fs/cgroup` is open in the container (`/proc/<pid>/fd`), also with `--init` (whose PID 1 never execs, so `O_CLOEXEC` alone would not close a leak);
  - a plain `run --memory 64M -- /bin/true` exits 0 and prints no "clone3 unavailable" note;
  - memory hog with `--memory 32M` exits 137 with the OOM message; `--pids 20` fork loop stays <= 20 processes and `pids.peak <= 20`; `--cpus 0.5` busy loop uses 35-65 % of a core (small versions of the Phase 3 checks);
  - after each: no leftover cgroup or veth.
- **strace** (optional): `strace -f -e trace=clone3` shows `CLONE_INTO_CGROUP` by default and not with `MINIBOX_NO_CLONE3=1`. With `-e inject=clone3:error=ENOSYS` and `:error=EPERM` the kernel refuses clone3 (like an old kernel or a seccomp filter):
  minibox prints `clone3 unavailable`, falls back, the container still runs inside `/minibox/box-<id>` (`cat /proc/self/cgroup`) and nothing is left.
- **failure injection:** `--memory abc`, `--memory 0`, `--cpus 0`, `--pids 0` give a clear `minibox:` message and a non-zero exit. A "fake rootfs" (a directory containing an empty `bin/busybox`) passes the Alpine check but cannot run anything, so the container fails *after* its
  cgroup (and, with `--net veth`, its veth) was prepared: non-zero exit with a message in both modes, no cgroup, no veth, no state. A failure of a cgroup file write itself cannot be injected safely, so that cleanup path is covered by these cases.
- **c. timing (information only):** 20 interleaved `run --net none --memory 256M --cpus 1 --pids 64 -- /bin/true` per mode after one warm-up each; prints median and minimum in ms side by side. Never fails; numbers depend on the machine.
  `--net none` is passed explicitly and the header line of the table says so (`run --net none /bin/true, 20 runs, limits`). It is also the default, but a veth network setup costs far more than the cgroup step (see `docs/BENCHMARKS.md`, veth row) and would hide the difference between the clone3 path and the `cgroup.procs` migration path. If a timing table ever shows nearly equal medians for both modes at a veth-sized value, check the header line first.
- **e. leftovers:** no `box-*` cgroup, no `vethh*` interface, `ps -a` empty and the state directory holding nothing but `.lock` (`state_clean` from `tests/lib.sh`; minibox keeps `.lock` on purpose).

## System calls used and why
Only what the commands it runs use. `strace` (ptrace) observes `clone3`; `-e inject` makes it fail on purpose.

## OS concept
Cgroup migration versus creating a process inside a cgroup; graceful fallback when a system call is not available; cleanup on error paths.

## How to test
`sudo bash tests/clone3_test.sh` (expect `ALL CHECKS PASSED`), or as part of `sudo bash tests/run_all.sh`. Syntax check without root: `bash -n tests/clone3_test.sh`.
