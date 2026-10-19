# tests/lib.sh

## Purpose
Helpers shared by `tests/phase4b_test.sh` and `tests/phase5_test.sh` (both `source` it). It is not run on its own. The sourcing script must define
`report` and, optionally, `CG_BASE` (default `/sys/fs/cgroup/minibox`).

**Why live processes, not `pids.current`.** When a command started by `minibox exec` is killed, its children become orphans and the kernel
reparents them to the container's PID 1. In minibox PID 1 is the user's command (e.g. `sleep 300`) and never calls `wait()`, so a dead orphan stays
a *zombie*: not running, but still in the process table. `pids.current` counts zombies, `cgroup.procs` lists only live processes. The tests ask
"is anything still running?", so they count the lines of `cgroup.procs`. (In a container started with `run --init`, PID 1 is `minibox-init`, which reaps orphans, so `zombie_count` is 0 there; `tests/init_test.sh` relies on that.)

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `live_count id` | container ID | prints the number of lines in `box-<id>/cgroup.procs` | prints 999 if the file cannot be read |
| `wait_live id max seconds` | ID, limit, timeout | 0 once the live count is at most `max`, 1 on timeout | sleeps 0.1 s between polls |
| `proc_field pid n` | host PID, field number | field `n` of `/proc/<pid>/stat` after the `(comm)` part (1 = state, 2 = parent PID) | empty if the process is gone |
| `zombie_count id` | container ID | `pids.current` minus the live count | – |
| `note_zombies id` | container ID | prints an `INFO:` line with that number; never a failure | – |
| `dump_cgroup id` | container ID | prints every process in the cgroup: host PID, state, command line, then the zombie `INFO:` line | – |
| `report_live name rc id baseline` | check name, status, ID, baseline | calls `report` with the live count and baseline; on failure also `dump_cgroup` | counts a failure through `report` |
| `zombies_of ppid` | host PID | prints the number of zombies (state `Z`) whose parent is that PID, by scanning `/proc/*/stat` | – |

## System calls used and why
None directly. It reads `/sys/fs/cgroup/minibox/box-<id>/{cgroup.procs,pids.current}` and `/proc/<pid>/{stat,cmdline}` (which use `open`/`read`).

## OS concept
Zombie processes, orphan reparenting to the PID namespace's init, and the difference between "tasks that exist" (`pids.current`) and
"processes that are alive" (`cgroup.procs`).

## How to test
It is exercised by `sudo tests/phase4b_test.sh` and `sudo tests/phase5_test.sh`. Without root: `bash -n tests/lib.sh`, or source it with
`CG_BASE` pointing at a temporary directory holding a fake `box-x/cgroup.procs` and `pids.current`.
