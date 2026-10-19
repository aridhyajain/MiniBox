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
sudo ./minibox run [--rootfs DIR] [--hostname NAME] [--net none|veth] -- COMMAND [ARGS...]

# example: isolated shell, loopback only
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh

# example: also a veth link (container 10.0.0.2 <-> host 10.0.0.1)
sudo ./minibox run --rootfs rootfs --net veth -- /bin/sh
```

Defaults: `--rootfs rootfs`, `--hostname minibox`, `--net none`. The exit code is the command's exit code.
Isolation: PID, UTS, mount (with `pivot_root` into the Alpine rootfs), IPC and network namespaces.
`--net none` gives only a loopback interface; `--net veth` adds a veth pair to the host (needs the host `ip` command; one such container at a time).

```sh
sudo tests/phase1_test.sh    # PID/UTS/mount/pivot_root checks
sudo tests/phase2_test.sh    # IPC/network/veth checks
```

## Documentation

See `docs/ARCHITECTURE.md`, `docs/VIVA_NOTES.md` and `docs/DEVLOG.md`. Every source file has a matching doc under `docs/`.
