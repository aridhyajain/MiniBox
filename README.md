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
sudo ./minibox run [--rootfs DIR] [--hostname NAME] -- COMMAND [ARGS...]

# example
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh
```

Defaults: `--rootfs rootfs`, `--hostname minibox`. The exit code is the command's exit code.
Phase 1 provides PID, UTS and mount namespace isolation with `pivot_root` into the Alpine rootfs.

```sh
sudo tests/phase1_test.sh    # automated checks
```

## Documentation

See `docs/ARCHITECTURE.md`, `docs/VIVA_NOTES.md` and `docs/DEVLOG.md`. Every source file has a matching doc under `docs/`.
