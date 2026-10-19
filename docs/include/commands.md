# include/commands.h

## Purpose
Public interface of the subcommands (used by `src/main.c`, implemented in `src/commands.c`).

## Declarations
`int cmd_run(int argc, char **argv)`, `cmd_ps`, `cmd_logs`, `cmd_stop`, `cmd_rm`. `argv[0]` is the subcommand name.
`int require_root(void)` – 0 if running as root, else prints "must be run as root" and returns -1 (also used by `src/exec.c`).
Return value of the `cmd_*` functions: process exit status (0 success, 1 error or usage error; `cmd_run` returns the container command's
exit status for a foreground container).

Details: `docs/src/commands.md`.

## System calls used
None (declarations only).

## OS concept
CLI layer on top of the runtime.

## How to test
`make`; `./minibox ps -x` prints the `ps` usage.
