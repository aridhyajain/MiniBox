# Makefile

## Purpose
Single entry point for building, setup and test instructions.

## Variables
`CC=gcc`, `CFLAGS=-std=c11 -Wall -Wextra -Werror -g -Iinclude -MMD -MP`, `BUILD=build`,
`TARGET=minibox`, `SRCS=src/main.c src/commands.c src/options.c src/monitor.c src/state.c src/container.c src/rootfs.c src/net.c src/cgroup.c src/exec.c src/minit.c`, `OBJS` = `build/*.o`.
`-MMD -MP` write `build/*.d` files so objects rebuild when a header changes (`-include` at the bottom).

## Targets
| Target | Does | Errors / side effects |
|--------|------|-----------------------|
| `all` (default) | builds `./minibox` | compiler warnings are errors |
| `minibox` | links the objects | – |
| `build/%.o` | compiles one source file | creates `build/` |
| `check-env` | runs `scripts/check_env.sh` | non-zero exit if a check fails |
| `rootfs` | runs `scripts/get_rootfs.sh` | exit 2 = run the printed sudo command |
| `test` | builds, then prints the sudo commands for the test scripts (including `tests/init_test.sh` and `tests/clone3_test.sh`) | never runs sudo itself |
| `test-all` | builds, then prints `sudo bash tests/run_all.sh` (runs every test script, prints a summary) | never runs sudo itself |
| `test-phase3` | builds, then prints `sudo tests/phase3_test.sh` | never runs sudo itself |
| `test-phase4` | builds, then prints `sudo tests/phase4_test.sh` | never runs sudo itself |
| `test-phase4b` | builds, then prints `sudo tests/phase4b_test.sh` | never runs sudo itself |
| `test-phase5` | builds, then prints the commands for the UI tests (`scripts/setup_ui.sh`, `sudo tests/phase5_test.sh`, the unit tests) | never runs sudo itself |
| `test-phase6` | builds, then prints `sudo bash tests/phase6_test.sh` | never runs sudo itself |
| `test-init` | builds, then prints `sudo bash tests/init_test.sh` | never runs sudo itself |
| `test-clone3` | builds, then prints `sudo bash tests/clone3_test.sh` | never runs sudo itself |
| `test-bench` | runs `python3 -m unittest tests/test_bench_stats.py -v` (no root) | – |
| `bench` | builds, then prints `sudo python3 bench/bench.py all` (full run, N=30) | prints only; never runs sudo itself |
| `bench-quick` | builds, then prints `sudo python3 bench/bench.py all --quick` | prints only; never runs sudo itself |
| `probe-attach` | builds, then prints `sudo bench/probe_attach_stall.sh bench/results/analysis/my_probe.txt` (the file name must not exist yet) | prints only; never runs sudo itself |
| `charts` | runs `python3 bench/make_charts.py bench/results/latest` (no root) | error if no results exist yet |
| `ui-setup` | prints `scripts/setup_ui.sh` (creates `ui/venv`, no sudo) | prints only |
| `ui-run` | builds, then prints `sudo scripts/run_ui.sh 8080` | prints only; never runs sudo itself |
| `demo` | builds, then prints the commands to run the Review 1 demo | never runs sudo itself |
| `deliverables-check` | `python3 scripts/build_deliverables.py check-tools` | no root; prints which converters exist and the exact install commands if one is missing |
| `deliverables-docs` | `python3 scripts/build_deliverables.py docs` | no root; writes only to `~/minibox-deliverables/` (outside the repo) |
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
make test       # prints the sudo commands
make test-phase3
make test-phase4
make test-phase4b
make test-phase5
make test-init   # prints: sudo bash tests/init_test.sh
make test-clone3 # prints: sudo bash tests/clone3_test.sh
make probe-attach # prints: sudo bench/probe_attach_stall.sh bench/results/analysis/my_probe.txt
make ui-setup    # prints scripts/setup_ui.sh
make ui-run      # prints sudo scripts/run_ui.sh 8080
make test-all   # prints: sudo bash tests/run_all.sh
make deliverables-check   # tool report and routes
make deliverables-docs    # builds ~/minibox-deliverables/MiniBox_Documentation.docx
make clean
```
