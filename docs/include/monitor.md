# include/monitor.h

## Purpose
Public interface of the monitor module (used by `src/commands.c`, implemented in `src/monitor.c`).

## Declarations
- `int monitor_run_foreground(struct container_config *cfg, struct container_state *st)` – run the container in this
  process, then delete its state. Returns `container_run`'s result.
- `int monitor_start_detached(struct container_config *cfg, struct container_state *st)` – fork a detached monitor
  and wait until the container started. Returns 0, or 1 after showing the log and removing the state.
- Both expect a state registered with `state_create()` and `cfg->id` / `cfg->net_index` already set.

Details: `docs/src/monitor.md`.

## System calls used
None (declarations only).

## OS concept
Interface to the supervisor process.

## How to test
`make`; behaviour via `sudo tests/phase4_test.sh`.
