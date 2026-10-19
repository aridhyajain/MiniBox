# src/options.c

## Purpose
Parses and validates the options of `minibox run` (moved out of the old `main.c`, plus the new `-d` and `--name`).
Fills a `struct run_options` (a `container_config`, the `detach` flag and the optional name).

`minibox run [-d] [--name NAME] [--rootfs DIR] [--hostname NAME] [--net none|veth] [--memory N[K|M|G]] [--cpus X] [--pids N] [--stats] -- COMMAND [ARGS...]`

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int options_parse_run(int argc, char **argv, struct run_options *opts)` | arguments, `argv[0]` = `"run"` | 0 / -1 | zeroes `opts`; defaults rootfs `rootfs`, hostname `minibox`, net `none`, no limits, foreground; `--stats` and `-d` are flags without a value; everything after `--` is the command. -1 for a missing `--`/command/value (no message, the caller prints usage) or an invalid value (specific message) |
| `int options_validate_rootfs(const char *path, char *resolved)` | path, `PATH_MAX` buffer | 0 / -1 | `realpath`; must be a directory containing `bin/busybox` |
| static `parse_net_mode(value, &mode)` | `none` / `veth` | 0 / -1 | prints `invalid --net` |
| static `size_multiplier(suffix)` | text after the number | 1, 2^10, 2^20, 2^30 or 0 | accepts `""`, `K`, `M`, `G` (either case) |
| static `parse_memory(s, &bytes)` | e.g. `64M` | 0 / -1 | digits first, valid suffix, no overflow, ≥ 1 MiB |
| static `parse_cpus(s, &quota_us)` | e.g. `0.5` | 0 / -1 | `strtod`; ≥ 0.01 and ≤ online CPUs (`sysconf`); quota = cpus × 100000 |
| static `parse_pids(s, &n)` | e.g. `32` | 0 / -1 | integer 1 … 4194304 |
| static `parse_name(value, &name)` | `--name` value | 0 / -1 | must satisfy `state_valid_name` (uniqueness is checked later, in `state_create`) |
| static `parse_option(opt, value, cfg, &name)` | one `--option value` pair | 0 / -1 | dispatches to the parsers; prints `unknown option` |

## System calls used and why
- `realpath`, `stat` – resolve and check the rootfs.
- `sysconf(_SC_NPROCESSORS_ONLN)` – upper bound for `--cpus`.

## OS concept
Translating human units into kernel interfaces (bytes for `memory.max`, quota/period for `cpu.max`); validating
input before any kernel object is created.

## How to test
```sh
./minibox run --memory 64X -- sh         # invalid --memory, exit 1 (no root needed)
./minibox run --name 'bad name' -- sh    # invalid --name
./minibox run --cpus 1000 -- sh          # more than online CPUs
sudo ./minibox run --rootfs /tmp -- sh   # not an Alpine rootfs
```
