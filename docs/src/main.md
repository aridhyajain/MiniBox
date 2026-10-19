# src/main.c

## Purpose
Entry point. Parses `minibox run [--rootfs DIR] [--hostname NAME] [--net none|veth] -- COMMAND [ARGS...]`,
checks preconditions (root, valid rootfs), builds a `struct container_config`
and calls `container_run()`. The process exit code is the container command's exit code.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `static void usage(const char *prog)` | program name | – | prints usage to stderr |
| `static int parse_net_mode(const char *value, enum net_mode *mode)` | `none` or `veth` | 0 / -1 if unknown | sets `*mode` |
| `static int parse_args(int argc, char **argv, struct container_config *cfg)` | argv, config to fill | 0 ok, -1 usage error | Defaults: rootfs `rootfs`, hostname `minibox`, net `none`; `--net` accepts only `none`/`veth`. Fails if subcommand isn't `run`, an option lacks a value, an option is unknown, `--` is missing, or no command follows `--`. `cfg->argv` points into `argv` (no copy). |
| `static int require_root(void)` | – | 0 if euid is 0, else -1 | prints "must be run as root" |
| `static int validate_rootfs(const char *path, char *resolved)` | user path, `PATH_MAX` buffer | 0 ok, -1 error | `realpath` to an absolute path; must be a directory containing `bin/busybox`; errors printed with `perror`, hint to run `make rootfs` |
| `int main(int argc, char **argv)` | – | container's exit status; 1 on any minibox error | – |

Error order: usage error → not root → invalid rootfs; all exit 1.

## System calls used and why
- `geteuid(2)` – namespaces/mounts/pivot_root need `CAP_SYS_ADMIN`; fail early with a clear message.
- `realpath(3)` (uses `readlink`/`lstat`) – the child changes directory and mount points, so an absolute path avoids ambiguity.
- `stat(2)` – check rootfs is a directory and `bin/busybox` exists.

## OS concept
Privilege: creating namespaces and mounting filesystems are privileged operations, enforced by Linux capabilities.

## How to test
```sh
./minibox                                   # usage, exit 1
./minibox run -- /bin/sh                    # "must be run as root", exit 1
sudo ./minibox run --rootfs nonexistent -- /bin/sh   # perror, exit 1
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh   # works
./minibox run --net bogus -- sh             # usage, exit 1
sudo ./minibox run --net veth -- /bin/sh    # veth mode
```
