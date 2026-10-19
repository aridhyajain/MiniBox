# scripts/get_rootfs.sh

## Purpose
Downloads the latest Alpine Linux x86_64 minirootfs tarball (~3 MB) and extracts
it into `rootfs/`, which later becomes the container's `/`.

## Behaviour (no user-defined functions except two helpers)
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `die msg` | message | exits 1 | prints `error: msg` to stderr |
| `fetch url out` | URL, output path | wget/curl status | writes file; dies if neither tool exists |

Main flow:
1. Create `.cache/` (tarball cache) and `rootfs/`.
2. Download `latest-releases.yaml`, extract the `alpine-minirootfs-*-x86_64.tar.gz` name.
3. Download the tarball (skipped if already cached) and verify its `.sha256`.
4. If not root, print `sudo tar -xzf ... -C rootfs` and exit **2**; if root, extract.

Exit codes: 0 extracted, 1 error (network, checksum, extraction), 2 needs root.
Side effects: network access to `dl-cdn.alpinelinux.org`; files in `.cache/` and `rootfs/`.

## System calls / commands used and why
- `wget`/`curl` – HTTP download. `sha256sum` – integrity check.
- `tar -xzf` (`open`, `write`, `mknod`, `chown`) – extraction; `chown` to uid 0 is
  why root is needed, so files inside the container appear root-owned.
- `id -u` (`geteuid(2)`) – detect whether running as root.

## OS concept
A container's filesystem is just a directory tree; Alpine's minirootfs is a
complete minimal userland (busybox, musl) that runs on the host kernel.
File ownership/permissions (uid 0) matter for the later `pivot_root`.

## How to test
```sh
scripts/get_rootfs.sh        # prints the sudo command (exit 2)
sudo tar -xzf <printed args> # run the printed command yourself
ls rootfs/bin/busybox rootfs/etc/alpine-release
```
