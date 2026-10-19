# tests/phase4b_test.sh

## Purpose
End-to-end check of `minibox exec`. Run with sudo; it never calls sudo itself. Needs `./minibox`, the Alpine rootfs and the
host tools `ip` and `hostname`. Takes about 30 seconds. It sets `MINIBOX_STATE_DIR` to a `mktemp -d` directory and removes it
at the end; an `EXIT` trap runs `rm -f` on every container still listed (also after a failed check or Ctrl-C). It refuses to
start if `box-*` cgroups or `vethh*` interfaces already exist. Only busybox applets run inside the containers.

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`, counts failures |
| `run_d [options] -- cmd...` | `minibox run -d --rootfs rootfs ...`, stdin closed |
| `exec_in target cmd...` | `minibox exec target -- cmd...`, stdin closed |
| `field id key` | one value from the container's state file |
| `wait_status id status seconds` | polls the state file |
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
8. After `stop`, `exec` on A fails with "not running"; `rm` of both removes their state; no `box-*` cgroup and no `vethh*` remain.

## System calls used and why
None directly; it exercises `setns`, `fork`, `chroot`, cgroup writes and the earlier commands.

## OS concept
Joining existing namespaces and cgroups; process exit statuses.

## How to test
```sh
make && sudo tests/phase4b_test.sh
```
