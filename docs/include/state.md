# include/state.h

## Purpose
Public interface of the state module (used by `src/commands.c`, `src/monitor.c`, `src/options.c`; implemented in `src/state.c`).

## Declarations
- Constants: `STATE_DEFAULT_ROOT` (`/var/lib/minibox/containers`), `STATE_ENV_VAR` (`MINIBOX_STATE_DIR`),
  `STATE_ID_LEN` (12), `STATE_NAME_MAX` (63), `STATE_CMD_MAX` (1024), `STATUS_RUNNING`/`STATUS_EXITED`/`STATUS_DEAD`.
- `struct container_state` – everything in `<root>/<id>/state`: `id`, `name`, `status`, `pid`, `monitor_pid`,
  `exit_code`, `created_at`, `started_at`, `finished_at`, `command`, `rootfs`, `hostname`, `net`, `memory` (bytes),
  `cpus` (cores), `pids`, `ip` (container address).
- Functions: `state_root`, `state_valid_name`, `state_init_from_config`, `state_create`, `state_save`, `state_load`,
  `state_list`, `state_find`, `state_remove`, `state_path`, `state_copy_fd`, `process_alive`, `state_pid_is_ns_init`,
  `state_effective_status`, `state_net_index`.

Signatures, return values, errors and side effects: `docs/src/state.md`.
Note: include `<limits.h>` users need `_GNU_SOURCE` (as every `.c` file here defines) for `PATH_MAX`.

## System calls used
None (declarations only).

## OS concept
Interface to the on-disk container database.

## How to test
`make`; behaviour via `sudo tests/phase4_test.sh`.
