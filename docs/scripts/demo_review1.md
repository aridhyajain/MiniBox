# scripts/demo_review1.sh

## Purpose
Interactive demo for Review 1 (about 5 minutes). For each step it prints a heading, one plain-language sentence
about what the step proves, and the exact command(s), then waits for Enter and runs them. Run with sudo:
```sh
sudo scripts/demo_review1.sh          # Enter between steps
sudo scripts/demo_review1.sh --auto   # waits 2 s instead of Enter (for screen recording)
```
Exits 1 with a clear message if not root, if `./minibox` is missing (run `make`), if `rootfs/bin/busybox` is
missing (run `make rootfs`), or if a host tool (`ip`, `ipcmk`, `ipcs`, `ipcrm`) is missing.

## Functions
| Function | Inputs | Behaviour |
|----------|--------|-----------|
| `step heading why cmd...` | heading, one-sentence explanation, one or more command strings | prints heading, sentence and all commands; waits (Enter, or `sleep 2` with `--auto`); then `eval`s each command with stdin from `/dev/null`; prints `[exit status N]` if a command exits non-zero (expected for the OOM step, 137). Always returns 0 so the demo continues. |
| EXIT trap | – | removes the host IPC queue (`ipcrm -q $ID`) if the demo is interrupted between creating and removing it |

Commands with nested quotes (`CMD_HOST_NS`, `CMD_BOX_NS`, `CMD_FORK`, `CMD_HOG`, `CMD_CPU`) are stored literally in
here-documents, so the command shown on screen is exactly the command that runs.

## Steps
| # | Shows | Approx. time |
|---|-------|--------------|
| 1 | host vs container hostname and `/proc/self/ns/{pid,uts,mnt}` IDs | 30 s |
| 2 | inside: hostname, `echo $$` = 1, `ps`, `ls /`, host `/home` not visible | 40 s |
| 3 | network: only `lo`; `--net veth` and `ping 10.0.0.1` (each output preceded by a label line) | 40 s |
| 4 | IPC: host `ipcmk -Q` queue not visible in container, then removed | 30 s |
| 5 | `--memory 32M --stats`: OOM kill, exit 137 | 40 s |
| 6 | `--pids 20 --stats`: fork bomb contained | 30 s |
| 7 | `--cpus 0.5 --stats`: ~1.5 s CPU in 3 s (the container shell runs `exec 2>/dev/null` so the "Terminated" message from `timeout` is hidden; the `[stats]` lines come from minibox and stay visible) | 30 s |
| 8 | cleanup proof: `cgroups left: N` (count of `box-*`, expect 0) and `veth interfaces left: N` (expect 0) | 20 s |

Inside containers only busybox tools are used.

## System calls / commands used and why
None directly; it drives `minibox` (which uses `clone`, `pivot_root`, cgroup file I/O) and the host tools `readlink`
(`/proc/self/ns/*` symlinks show namespace IDs), `ipcmk`/`ipcs`/`ipcrm` (SysV IPC), `ip` (interface list).

## OS concept
Demonstrates namespaces (what a process sees) and cgroups (what it can use) end to end.

## How to test
```sh
bash -n scripts/demo_review1.sh               # syntax
scripts/demo_review1.sh                       # without sudo: "must run as root", exit 1
sudo scripts/demo_review1.sh --auto           # full run; every step should show the expected output
```
