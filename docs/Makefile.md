# Makefile

## Purpose
Single entry point for building and setup tasks. In Phase 0 `all` is a placeholder.

## Variables
`CC=gcc`, `CFLAGS=-std=c11 -Wall -Wextra -Werror -g`, `BUILD=build`.

## Targets
| Target | Does | Errors / side effects |
|--------|------|-----------------------|
| `all` | prints a placeholder message | none |
| `check-env` | runs `scripts/check_env.sh` | non-zero exit if a check fails |
| `rootfs` | runs `scripts/get_rootfs.sh` | exit 2 means run the printed sudo command |
| `clean` | `rm -rf build` | deletes build outputs only (never `rootfs/`) |

All targets are `.PHONY`.

## System calls used and why
None directly; `make` uses `fork`/`execve`/`wait` to run recipe commands.

## OS concept
Process creation (`fork`+`exec`) by a build tool; dependency-driven builds (more relevant once C sources exist).

## How to test
```sh
make            # placeholder message
make check-env  # environment report
make clean      # no error even if build/ is missing
```
