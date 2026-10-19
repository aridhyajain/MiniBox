# tests/phase2_test.sh

## Purpose
End-to-end check of Phase 2: IPC namespace, network namespace, loopback and the veth link.
Run with sudo (it exits 1 with a message otherwise; it never calls sudo itself). Needs `./minibox`,
`rootfs/bin/busybox`, and on the host: `ip`, `ping`, `ipcmk`, `ipcs`, `ipcrm`. Takes about 15 seconds.
Inside the container it uses Alpine's busybox applets `ip`, `ping`, `ipcs` (all present, so nothing needed installing).

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`; counts failures |
| `box cmd...` | runs a command in a default container (loopback only) |
| `vbox cmd...` | same with `--net veth` |
| `veth_count` | number of `vethh*` interfaces on the host |
| `wait_no_veth` | polls up to 3 s for them to disappear (kernel teardown is asynchronous) |
| `cleanup` | `ipcrm -q` of the host test queue; also run by `trap ... EXIT` |

## Checks
1. `ip link` inside a default container lists only `lo`.
2. `lo` is UP.
3. `ping -c 1 127.0.0.1` works inside.
4. A message queue created on the host with `ipcmk -Q` is not listed by `ipcs -q` inside (it is removed afterwards).
5. `--net veth`: container pings 10.0.0.1.
6. `--net veth`: container's `eth0` has 10.0.0.2/24.
7. `--net veth`: the host pings 10.0.0.2 while a `sleep 8` container is running.
8. No `vethh*` interface remains after the container exits.
9. A failing command (`nosuchcmd`) exits 127 under `--net veth` and still leaves no `vethh*`.

## System calls used and why
None directly; exercises `clone` with `CLONE_NEWIPC`/`CLONE_NEWNET`, socket ioctls, and `ip` via `minibox`.

## OS concept
Black-box verification of IPC and network namespace isolation.

## How to test
```sh
make && sudo tests/phase2_test.sh
```
