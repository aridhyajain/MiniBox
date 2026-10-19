# minibox

A minimal container runtime in C using Linux namespaces and cgroups v2 (OS lab project).

## Prerequisites

- WSL2 (Ubuntu) or native Linux with cgroup v2
- gcc, make, python3, wget or curl
- Run `make check-env` to verify

## Setup

```sh
make check-env   # verify the environment
make rootfs      # download Alpine minirootfs into rootfs/
```

## Build

TODO

## Run

TODO

## Documentation

See `docs/ARCHITECTURE.md` and `docs/DEVLOG.md`. Every source file has a matching doc under `docs/`.
