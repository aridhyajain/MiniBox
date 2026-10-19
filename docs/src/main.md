# src/main.c

## Purpose
Entry point. Parses
`minibox run [--rootfs DIR] [--hostname NAME] [--net none|veth] [--memory N[K|M|G]] [--cpus X] [--pids N] [--stats] -- COMMAND [ARGS...]`,
validates every value, checks preconditions (root, valid rootfs), fills a `struct container_config`
and calls `container_run()`. The process exit code is the container command's exit code.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `static void usage(const char *prog)` | program name | – | prints usage to stderr |
| `static int parse_net_mode(const char *value, enum net_mode *mode)` | `none` / `veth` | 0 / -1 | prints `invalid --net` |
| `static unsigned long long size_multiplier(const char *suffix)` | text after the number | 1, 2^10, 2^20, 2^30, or 0 if invalid | accepts `""`, `K`, `M`, `G` (either case) |
| `static int parse_memory(const char *s, long long *bytes)` | e.g. `64M` | 0 / -1 | must start with a digit, valid suffix, no overflow, ≥ 1 MiB; prints a specific error |
| `static int parse_cpus(const char *s, long *quota_us)` | e.g. `0.5`, `2` | 0 / -1 | `strtod`; must be ≥ 0.01 (kernel minimum quota 1 ms) and ≤ online CPUs (`sysconf`); quota = cpus × 100000 rounded |
| `static int parse_pids(const char *s, long *pids)` | e.g. `32` | 0 / -1 | integer 1 … 4194304 |
| `static int parse_option(const char *opt, const char *value, struct container_config *cfg)` | one option + value | 0 / -1 | dispatches to the parsers; prints `unknown option` |
| `static int parse_args(int argc, char **argv, struct container_config *cfg)` | argv, config | 0 / -1 | zeroes cfg; defaults rootfs `rootfs`, hostname `minibox`, net `none`, no limits; `--stats` is a flag without a value; requires `run` and `--` + command |
| `static int require_root(void)` | – | 0 / -1 | euid must be 0 |
| `static int validate_rootfs(const char *path, char *resolved)` | path, `PATH_MAX` buffer | 0 / -1 | `realpath`; directory with `bin/busybox` |
| `int main(int argc, char **argv)` | – | container status; 1 on any minibox error | warns if `--stats` is given without a limit (no cgroup → no stats) |

Error order: usage/validation error → not root → invalid rootfs; all exit 1. Validation runs before the root
check, so bad values can be tested without sudo.

## System calls used and why
- `geteuid` – namespaces, mounts, cgroups need root.
- `realpath`, `stat` – resolve and check the rootfs.
- `sysconf(_SC_NPROCESSORS_ONLN)` – upper bound for `--cpus`.

## OS concept
Privilege checks; translating human units into kernel interfaces (bytes for `memory.max`, quota/period for `cpu.max`).

## How to test
```sh
./minibox run --memory 64X -- sh     # invalid --memory, exit 1
./minibox run --cpus 0 -- sh         # invalid --cpus
./minibox run --cpus 1000 -- sh      # more than online CPUs
./minibox run --pids 0 -- sh         # invalid --pids
./minibox run --net bogus -- sh      # invalid --net
./minibox run -- /bin/sh             # must be run as root
sudo ./minibox run --rootfs rootfs --memory 64M --cpus 0.5 --pids 32 --stats -- /bin/sh
```
