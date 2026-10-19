# Makefile

## Purpose
Single entry point for building, setup and test instructions.

## Variables
`CC=gcc`, `CFLAGS=-std=c11 -Wall -Wextra -Werror -g -Iinclude -MMD -MP`, `BUILD=build`,
`TARGET=minibox`, `SRCS=src/main.c src/container.c src/rootfs.c src/net.c`, `OBJS` = `build/*.o`.
`-MMD -MP` write `build/*.d` files so objects rebuild when a header changes (`-include` at the bottom).

## Targets
| Target | Does | Errors / side effects |
|--------|------|-----------------------|
| `all` (default) | builds `./minibox` | compiler warnings are errors |
| `minibox` | links the objects | – |
| `build/%.o` | compiles one source file | creates `build/` |
| `check-env` | runs `scripts/check_env.sh` | non-zero exit if a check fails |
| `rootfs` | runs `scripts/get_rootfs.sh` | exit 2 = run the printed sudo command |
| `test` | builds, then prints `sudo tests/phase1_test.sh` and `sudo tests/phase2_test.sh` | never runs sudo itself |
| `clean` | `rm -rf build minibox` | never touches `rootfs/` |

All non-file targets are `.PHONY`.

## System calls used and why
None directly; `make` uses `fork`/`execve`/`wait` to run recipes, and file timestamps (`stat`) to decide what to rebuild.

## OS concept
Process creation by a build tool; dependency-driven incremental builds.

## How to test
```sh
make            # compiles with no warnings, creates ./minibox
make            # second run: "Nothing to be done"
touch include/rootfs.h && make   # rebuilds container.o and rootfs.o
make test       # prints the sudo command
make clean
```
