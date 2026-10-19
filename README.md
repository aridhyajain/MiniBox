# minibox

A minimal container runtime in C using Linux namespaces and cgroups v2 (OS lab project).

## Prerequisites

- WSL2 (Ubuntu) or native Linux with cgroup v2
- gcc, make, python3, wget or curl
- Run `make check-env` to verify

## Setup

```sh
make check-env   # verify the environment
make rootfs      # download Alpine minirootfs; prints a sudo tar command to run
```

## Build

```sh
make             # builds ./minibox (C11, -Wall -Wextra -Werror)
make test        # prints the command to run the tests
```

## Run

```sh
sudo ./minibox run [--rootfs DIR] [--hostname NAME] [--net none|veth] \
                   [--memory N[K|M|G]] [--cpus X] [--pids N] [--stats] -- COMMAND [ARGS...]

# example: isolated shell, loopback only
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh

# example: also a veth link (container 10.0.0.2 <-> host 10.0.0.1)
sudo ./minibox run --rootfs rootfs --net veth -- /bin/sh

# example: resource limits (cgroups v2) and a usage summary after exit
sudo ./minibox run --rootfs rootfs --memory 64M --cpus 0.5 --pids 32 --stats -- /bin/sh
```

Defaults: `--rootfs rootfs`, `--hostname minibox`, `--net none`. The exit code is the command's exit code.
Isolation: PID, UTS, mount (with `pivot_root` into the Alpine rootfs), IPC and network namespaces.
Limits: `--memory` (bytes, K/M/G suffix; no swap), `--cpus` (cores, e.g. 0.5), `--pids` (max processes).
With any limit the container runs in `/sys/fs/cgroup/minibox/box-<pid>`, removed on exit (also on Ctrl-C/SIGTERM).
Without limits no cgroup is created. An OOM-killed container exits with 137. `--stats` prints peak memory, CPU time, peak pids and OOM kills.
`--net none` gives only a loopback interface; `--net veth` adds a veth pair to the host (needs the host `ip` command; one such container at a time).

```sh
sudo tests/phase1_test.sh    # PID/UTS/mount/pivot_root checks
sudo tests/phase2_test.sh    # IPC/network/veth checks
sudo tests/phase3_test.sh    # cgroup limit checks
```

## Demo (Review 1)

```sh
sudo scripts/demo_review1.sh          # interactive, Enter between steps (~5 min)
sudo scripts/demo_review1.sh --auto   # 2 s pause per step, for recording
```

See `docs/REVIEW1.md` for the presentation guide and likely viva questions. `make demo` prints these commands.

## Documentation

See `docs/ARCHITECTURE.md`, `docs/VIVA_NOTES.md` and `docs/DEVLOG.md`. Every source file has a matching doc under `docs/`.
