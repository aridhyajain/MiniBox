# tests/phase4_test.sh

## Purpose
End-to-end check of Phase 4a (run -d, ps, logs, stop, rm, per-container subnets, dead-monitor cleanup). Run with sudo;
it never calls sudo itself. Needs `./minibox`, the Alpine rootfs and the host tools `ip`, `ping`, `timeout`, `python3`.
Takes about a minute. It sets `MINIBOX_STATE_DIR` to a `mktemp -d` directory, so your real containers are not
touched, and removes it at the end. It refuses to start if `box-*` cgroups or `vethh*` interfaces already exist.
An `EXIT` trap runs `rm -f` on every container still listed and deletes the temp files, also after a failure or Ctrl-C.

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`, counts failures |
| `run_d [options] -- cmd...` | `minibox run -d --rootfs rootfs ...`, stdin closed |
| `field id key` | one value from the container's state file |
| `wait_status id status seconds` | polls the state file |
| `wait_log id text seconds` | polls `minibox logs` for a string |
| `wait_no_veth` | polls until no `vethh*` exists (up to 3 s) |
| `veth_count` | number of `vethh*` host interfaces |
| `cleanup` | EXIT trap: `rm -f` all containers, delete temp dir/file |

## Checks
0. `ps --json` with no containers prints `[]`.
1. `run -d -- sh -c 'sleep 3'` prints only a 12-hex ID; `ps` shows it running with its command; after it exits `ps`
   hides it and `ps -a` shows `exited`.
2. The 3-line loop: `timeout 4 logs -f` shows `line1`; after exit `logs` shows line1..3.
3. `sleep 300` with `--pids 32 --net veth`: cgroup `box-<id>` and one `vethh*` exist; `stop --time 2` takes 2–6 s
   (PID 1 ignores SIGTERM, SIGKILL after 2 s), reports SIGKILL, state is `exited` with `exit_code=137`, and the cgroup and
   veth are gone (checked immediately, because the exit is recorded after cleanup).
4. Two `--net veth` containers at once get `10.0.0.2` and `10.0.1.2`; each pings its own host address (`10.0.0.1`,
   `10.0.1.1`) from inside (the ping runs in the container's command, its result is read from the log); two veths
   exist; after stopping both none remain; a third container gets `10.0.0.2` again (lowest free).
5. `--name web` works, a duplicate name is refused and adds nothing (the `ps -a` row count and the number of state directories are taken just before and after the attempt, because sections 1 and 2 leave their exited containers listed; both counts are printed on failure), `logs web` works, `rm` of a running
   container is refused, after `stop` `rm` removes the state directory. `ps -a --json` is valid JSON (`python3 -m json.tool`) and a command containing `"` and `\`
   round-trips exactly through the JSON.
6. `kill -9` of the monitor: `ps` shows `dead`; the init, `box-<id>` and the veth are still there; `rm -f` removes the
   cgroup, the veth, the init process and the state directory.

## Notes on the test design
- A container's own `ping` is how "can ping its host address" is checked, since there is no `exec` yet.
- `wait_no_veth` is used only after `rm -f` of a dead container (the kernel removes the veth a moment after the netns dies).

## System calls used and why
None directly; exercises `fork`/`setsid`/`clone`, `kill`, `rename`, `flock`, cgroup and veth cleanup.

## OS concept
Process supervision, signals to a PID-namespace init, on-disk state shared between processes.

## How to test
```sh
make && sudo tests/phase4_test.sh
```
