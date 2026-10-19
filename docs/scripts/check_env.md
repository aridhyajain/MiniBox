# scripts/check_env.sh

## Purpose
Checks that the machine can build and run minibox and prints a PASS/FAIL summary
with suggested fixes. Run as a normal user (never with sudo). Exit code 0 = all
required checks passed, 1 = at least one failed.

## Functions
Helpers (`pass`, `info`, `fail`) print results; `fail` also increments the global
`FAILS` counter and records a fix in the `FIXES` array (shell-script state, kept
simple on purpose).

| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `pass msg` / `info msg` | message | – | prints `[PASS]` / `[INFO]` |
| `fail msg fix` | message, fix text | – | prints `[FAIL]`, `FAILS++`, appends fix |
| `check_kernel` | – | – | parses `uname -r`; fails if kernel < 4.15 |
| `check_wsl` | – | – | reads `/proc/version`; fails on WSL1 (no `wsl2` in release) |
| `check_cgroup` | – | – | `stat -fc %T /sys/fs/cgroup` must be `cgroup2fs`; prints `cgroup.controllers`; requires `memory`, `cpu`, `pids` |
| `check_userns` | – | – | prints sysctl knobs, then tries `unshare -U true` |
| `check_tools` | – | – | looks for gcc, make, python3, and wget or curl |

The script is read-only: it changes nothing on the system.

## System calls / commands used and why
- `uname -r` (`uname(2)`) – kernel release.
- `stat -fc %T` (`statfs(2)`) – filesystem type magic; identifies cgroup v2.
- `unshare -U` (`unshare(2)` with `CLONE_NEWUSER`) – real test that user namespaces work.
- `/proc/version`, `/proc/sys/user/max_user_namespaces` – WSL detection and limits.

## OS concept
Kernel feature detection: namespaces and cgroup v2 are the two kernel facilities
a container runtime is built on; the check confirms both exist before any code is written.

## How to test
```sh
scripts/check_env.sh; echo "exit=$?"
```
Expect PASS lines and `exit=0` on a ready machine. To see a failure path, run
`PATH=/nonexistent /bin/bash scripts/check_env.sh` (tools reported missing).
