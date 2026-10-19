# tests/phase1_test.sh

## Purpose
End-to-end check of Phase 1 isolation. Run with sudo (it exits 1 with a message otherwise);
it never calls sudo itself. Requires `./minibox` (`make`) and `rootfs/bin/busybox` (`make rootfs`).

## Functions
| Function | Inputs | Returns / effects |
|----------|--------|-------------------|
| `report name status [detail]` | check name, 0=pass | prints `PASS:`/`FAIL:`, increments `FAILS` on failure |
| `box cmd...` | command | runs it via `minibox run --rootfs rootfs --hostname mybox -- cmd` with stdin from `/dev/null`, passes through output and exit code |

## Checks
1. Hostname inside is `mybox` and differs from the host's (UTS ns).
2. `echo $$` prints `1` (PID ns).
3. `ps` lists 1–5 processes, all with PID ≤ 10 (own `/proc`, PID ns).
4. `/proc/mounts` contains a `proc /proc proc` entry.
5. No entry of the host's `/home` appears inside, and `/etc/os-release` has `ID=alpine` (pivot_root).
6. `sh -c 'exit 42'` makes `minibox` exit 42.

Exit status: 0 if all pass, 1 otherwise.

## System calls used and why
None directly; exercises `clone`, `sethostname`, `mount`, `pivot_root`, `waitpid` through `minibox`.

## OS concept
Black-box verification of namespaces and mount-namespace root switching.

## How to test
```sh
make && sudo tests/phase1_test.sh
```
