# tests/phase6_test.sh

## Purpose
Checks the benchmark tooling end to end (not the numbers, which depend on the machine). Run as root: `sudo bash tests/phase6_test.sh` (about 1 minute, listed in `tests/run_all.sh`).

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `veth_count` | – | number of `vethh*` interfaces | – |
| `report name rc [detail]` | check name, status | prints `PASS:`/`FAIL:` | counts failures |

## Checks
1. `bench.py all --quick --out <tmp>` runs under `timeout 150` so the test itself can never hang (a temp directory, removed by an `EXIT` trap, so `bench/results/` is untouched). The duration is printed as `INFO:` (should stay under about 90 s); the checks are: did not hang, exits 0, and no `ERROR:`/`TIMEOUT:` line in the output (any such lines are printed first).
2. `startup_raw.csv`, `startup_summary.csv`, `memory.csv`, `memory_incremental.csv`, `limits.csv`, `system.json`, `summary.txt` exist and are non-empty; the timestamped directory and `latest/` exist.
3. `system.json` is valid JSON with `kernel` and `cpu_cores`.
4. At least four startup configurations measured; a `docker` row exists (measured or "not measured").
5. The log has a status line (`OK:`/`FAIL:`/`ERROR:`/`TIMEOUT:`) for `memory/`, `pids/` and `cpu/`; `limits.csv` has no `ERROR`/`TIMEOUT` rows (rows are listed if any). The OK/FAIL counts are `INFO:` only, because a `FAIL` (kernel result differs from expectation) depends on the machine. The section durations (`Section A/B/C`, `Total time`) are echoed as `INFO:`.
6. `make_charts.py` exits 0 and `startup.svg`, `memory.svg`, `limits.svg` parse as XML.
7. Afterwards: bench reported `CLEANUP: OK`, no `box-*` cgroup, no `vethh*` interface, no `minibox-bench-*` temp state directory. The script refuses to start if leftovers exist.

## System calls used and why
Through the tools: `ip` (netlink) to list interfaces, `ls`/`grep`, `python3`; `mktemp` for the output directory.

## OS concept
Resource cleanup: every cgroup, veth and state entry created by a program must be removed even on failure.

## How to test
`bash -n tests/phase6_test.sh` (syntax, no root); `sudo bash tests/phase6_test.sh` for the real run. Exit 0 on `ALL CHECKS PASSED`.
