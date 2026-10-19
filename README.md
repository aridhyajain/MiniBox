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
sudo ./minibox run [-d] [--name NAME] [--rootfs DIR] [--hostname NAME] [--net none|veth] \
                   [--memory N[K|M|G]] [--cpus X] [--pids N] [--stats] -- COMMAND [ARGS...]

# example: isolated shell, loopback only
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh

# example: also a veth link (container 10.0.n.2 <-> host 10.0.n.1; n = 0 for the first container)
sudo ./minibox run --rootfs rootfs --net veth -- /bin/sh

# example: resource limits (cgroups v2) and a usage summary after exit
sudo ./minibox run --rootfs rootfs --memory 64M --cpus 0.5 --pids 32 --stats -- /bin/sh
```

Defaults: `--rootfs rootfs`, `--hostname minibox`, `--net none`. The exit code is the command's exit code.
Isolation: PID, UTS, mount (with `pivot_root` into the Alpine rootfs), IPC and network namespaces.
Limits: `--memory` (bytes, K/M/G suffix; no swap), `--cpus` (cores, e.g. 0.5), `--pids` (max processes).
With any limit the container runs in `/sys/fs/cgroup/minibox/box-<id>`, removed on exit (also on Ctrl-C/SIGTERM).
Without limits no cgroup is created. An OOM-killed container exits with 137. `--stats` prints peak memory, CPU time, peak pids and OOM kills.
`--net none` gives only a loopback interface; `--net veth` adds a veth pair to the host (needs the host `ip` command).
Every veth container gets its own subnet `10.0.n.0/24` (the lowest free `n`), so several can run at once.

## Manage containers (Phase 4a)

Every container has an ID (12 hex characters) and a directory with its state in `/var/lib/minibox/containers/<id>/`
(override with `MINIBOX_STATE_DIR`). Commands that take `<id|name>` accept either.

```sh
# start in the background: prints only the ID; --name is optional but must be unique
sudo ./minibox run -d --name demo --memory 64M -- sh -c 'while :; do date; sleep 1; done'

sudo ./minibox ps                 # running (and dead) containers
sudo ./minibox ps -a              # also exited ones
sudo ./minibox ps --json          # JSON array, for scripts and the future web UI
# ID           NAME   STATUS   PID    COMMAND                  CREATED   LIMITS
# 3fa9c01be274 demo   running  4121   sh -c while :; do da...  12s ago   mem=64M

sudo ./minibox logs demo          # print the output so far
sudo ./minibox logs -f demo       # follow until the container exits (Ctrl-C leaves it running)
sudo ./minibox stop demo          # SIGTERM, then SIGKILL after 5 s (--time N changes that)
sudo ./minibox rm demo            # delete the state and log of a stopped container
sudo ./minibox rm -f demo         # stop it first if it is running
```

A plain `sleep` or `sh` as the container's PID 1 ignores SIGTERM (a PID-namespace init only gets signals it has a
handler for), so `stop` normally ends with SIGKILL and exit code 137. A foreground `minibox run` is registered while it
runs (so `ps` shows it) and its state is deleted when it ends. If a background container's monitor process is killed,
`ps` shows the container as `dead`; `rm` kills what is left of it and removes its cgroup and veth.

```sh
sudo tests/phase1_test.sh    # PID/UTS/mount/pivot_root checks
sudo tests/phase2_test.sh    # IPC/network/veth checks
sudo tests/phase3_test.sh    # cgroup limit checks
sudo tests/phase4_test.sh    # run -d / ps / logs / stop / rm checks
```

## Demo (Review 1)

```sh
sudo scripts/demo_review1.sh          # interactive, Enter between steps (~5 min)
sudo scripts/demo_review1.sh --auto   # 2 s pause per step, for recording
```

See `docs/REVIEW1.md` for the presentation guide and likely viva questions. `make demo` prints these commands.

## Documentation

See `docs/ARCHITECTURE.md`, `docs/VIVA_NOTES.md` and `docs/DEVLOG.md`. Every source file has a matching doc under `docs/`.
