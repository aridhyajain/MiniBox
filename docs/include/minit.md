# include/minit.h

## Purpose
Public interface of the container init (used by `src/container.c`, implemented in `src/minit.c`).

## Declarations
`int minit_run(char *const argv[])` – run `argv` as the child of a tiny PID 1: forward signals, reap every child, return the
command's exit status (128+N if killed by signal N; 127 if `fork` failed or the program was not found; 126 if it could not be executed).
Call it inside the container, as PID 1, after all setup, instead of `execvp`.

Details: `docs/src/minit.md`.

## System calls used
None (declaration only).

## OS concept
Interface between the container setup code and the PID-1 (init) behaviour.

## How to test
`make`; `sudo bash tests/init_test.sh`.
