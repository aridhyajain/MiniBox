# tests/phase4b_test.sh

## Purpose
End-to-end check of `minibox exec`. Run with sudo; it never calls sudo itself. Needs `./minibox`, the Alpine rootfs and the
host tools `ip` and `hostname`. Takes about 45 seconds. It sets `MINIBOX_STATE_DIR` to a `mktemp -d` directory and removes it
at the end; an `EXIT` trap runs `rm -f` on every container still listed (also after a failed check or Ctrl-C). It refuses to
start if `box-*` cgroups or `vethh*` interfaces already exist. Only busybox applets run inside the containers. It sources `tests/lib.sh`.

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`, counts failures |
| `run_d [options] -- cmd...` | `minibox run -d --rootfs rootfs ...`, stdin closed |
| `exec_in target cmd...` | `minibox exec target -- cmd...`, stdin closed |
| `field id key` | one value from the container's state file |
| `wait_status id status seconds` | polls the state file |
| `live_count`, `wait_live`, `report_live`, `zombies_of`, `note_zombies` | from `tests/lib.sh` (see `docs/tests/lib.md`): count live processes in `cgroup.procs`, not `pids.current` |
| `wait_no_veth` | polls until no `vethh*` exists (up to 3 s) |
| `veth_count` | number of `vethh*` host interfaces |
| `cleanup` | EXIT trap: `rm -f` all containers, delete the temp dir |

## Checks
Containers: A = `run -d --name worker --hostname boxy --memory 64M --pids 20 -- sleep 300`, B = `run -d --net veth -- sleep 300`.
1. Both IDs are 12 hex characters; A has `box-<id>`.
2. `exec worker -- hostname` prints `boxy`, not the host's name (also proves lookup by name).
3. `exec -- ps` lists PID 1 (`sleep`) and the exec'd `ps` itself; `exec -- sh -c 'echo $$'` prints a number ≤ 100 (a container PID, not a host PID).
4. While `exec -- sleep 5` runs in the background, `box-<id>/pids.current` on the host is ≥ 2; afterwards it is back to 1.
5. A file written with `sh -c 'echo hi > /tmp/x'` is read by a second `exec -- cat /tmp/x`, and is not in the host's `rootfs/tmp` (tmpfs).
   `echo piped | exec -- cat` prints `piped` (stdin inherited). `sh -c 'exit 7'` returns 7; a self-`kill -9` returns 137;
   an unknown program returns 127 with a message.
6. B: `exec -- ip addr show eth0` contains the state file's `10.0.n.2/`; A (`--net none`) has no `eth0`.
7. Errors: unknown container → "no such container"; `exec A` and `exec A --` → "no command"; an exited container → "not running";
   a normal user (via `setpriv`, uid 65534) → "must be run as root". The last check prints `SKIP:` when `setpriv` is missing or
   that user cannot execute `./minibox` (e.g. the home directory is mode 750).
8. **Dies with `minibox exec`** (stdin is `/dev/null`, so group mode). All counts are of **live** processes (lines in `box-<id>/cgroup.procs`); baseline = the live count of A before the execs (1, the init). `pids.current` is not used because it also counts zombies, and the container's PID 1 never reaps the orphans reparented to it (see `docs/tests/lib.md`).
   - `exec -- sleep 30` in the background: live count rises above the baseline; `kill -TERM` of that `minibox exec` brings it back within 2 s; `wait` returns 143.
   - `exec -- sh -c 'sleep 30 & sleep 30'`: at least baseline+2 live processes; `kill -TERM` removes the shell and **both** sleeps (grandchild, via the process group) within 2 s.
   - `kill -INT` (no tty) also removes the command within 2 s.
   - `kill -KILL` of `minibox exec -- sleep 30`: the direct child dies through `PR_SET_PDEATHSIG` within 2 s (live count back to baseline), the zombie part is **informational only** (`INFO:` lines, never PASS/FAIL, no effect on the exit code). Before the kill the zombie children of the init (state `Z`, parent = the init's host PID from the state file) are recorded; after it the script waits up to 1 s in 100 ms steps for a new one, then reports whether a new zombie appeared and whether the count went up, and always prints `pid ppid state cmd` for every child of the init and every `Z` process on the host. The SIGKILL case may leave a zombie under the container's init depending on timing and process layout.
   - Not asserted: a grandchild after SIGKILL of `minibox exec` survives (nothing can run after SIGKILL; PDEATHSIG is not inherited). Documented in `docs/src/exec.md`.
   - An `exec -- sh -c 'exit 5'` still returns 5, and nothing is left running in the cgroup afterwards.
   - When one of these checks fails, every process still in the cgroup is printed (host PID, state, command line). An `INFO:` line gives the number of unreaped zombies (`pids.current` minus live count); it is informational and never a failure.
   The tty path (`exec -- sh` on a terminal) cannot be scripted here; try it by hand (`sudo ./minibox exec <id> -- sh`).
9. After `stop`, `exec` on A fails with "not running"; `rm` of both removes their state; no `box-*` cgroup and no `vethh*` remain.

## System calls used and why
None directly; it exercises `setns`, `fork`, `chroot`, cgroup writes and the earlier commands.

## OS concept
Joining existing namespaces and cgroups; process exit statuses.

## How to test
```sh
make && sudo tests/phase4b_test.sh
```
