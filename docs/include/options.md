# include/options.h

## Purpose
Public interface of the option parser (used by `src/commands.c`, implemented in `src/options.c`).

## Declarations
- `DEFAULT_ROOTFS` (`"rootfs"`), `DEFAULT_HOSTNAME` (`"minibox"`).
- `struct run_options { struct container_config cfg; int detach; const char *name; }` – pointers refer to `argv`.
- `int options_parse_run(int argc, char **argv, struct run_options *opts)` – 0 / -1.
- `int options_validate_rootfs(const char *path, char *resolved)` – 0 / -1.

Details: `docs/src/options.md`.

## System calls used
None (declarations only).

## OS concept
Interface between the command line and the runtime configuration.

## How to test
`make`; `./minibox run --memory 64X -- sh` must print an error.
