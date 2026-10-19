# include/exec.h

## Purpose
Public interface of `minibox exec` (used by `src/main.c`, implemented in `src/exec.c`).

## Declarations
`int cmd_exec(int argc, char **argv)` – `argv[0]` is `"exec"`, followed by `<id|name> -- COMMAND [ARGS...]`.
Returns the command's exit status (128+N if killed by signal N, 127 if it cannot be executed) or 1 on a minibox error.

Details: `docs/src/exec.md`.

## System calls used
None (declaration only).

## OS concept
CLI layer on top of `setns()`.

## How to test
`make`; `./minibox exec` prints the usage.
