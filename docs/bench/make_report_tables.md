# bench/make_report_tables.py

## Purpose
Prints the report tables (Markdown) from a results directory written by `bench.py`, so that no benchmark number in `docs/BENCHMARKS.md` is typed by hand. Standard library only, no root.
Output: machine and run details (from `system.json`), the startup table (n, min, median, mean, p95, stdev), the memory table (per-container cgroup, init PSS, monitor PSS and total), the incremental memory cost, and the limit-enforcement table (check, expected, measured, status).
Values from the CSV files are copied as text, so they are not rounded again. The only conversion is memory from bytes to MiB (divided by 1048576, 2 decimals); the exact bytes of the per-container total are printed next to it.
A configuration that was not measured (docker) appears as a row with the reason from the file.

## Usage
`python3 bench/make_report_tables.py [RESULTS_DIR]` (default `bench/results/reference`). Exit 0 and the Markdown on stdout; exit 1 with a message on stderr if the directory does not exist. A missing CSV gives an empty table, a missing `system.json` an empty machine list.

`python3 bench/make_report_tables.py --compare BEFORE_DIR AFTER_DIR` prints one before/after startup table (columns: configuration, run, n, median, mean, p95, max, all copied as text from each directory's `startup_summary.csv`).
Rows, in this order: `minibox_nolimits` and `minibox_limits` from BEFORE; `minibox_nolimits`, `minibox_limits` and `minibox_limits_migrate` from AFTER; `minibox_veth_limits` from BEFORE, then from AFTER. A configuration that is absent or not `measured` in its file is printed as a row of `-` (nothing is made up).
Exit 1 with a message on stderr if the argument count is wrong, a directory does not exist, or it has no `startup_summary.csv`.
Example for the clone3 change: `python3 bench/make_report_tables.py --compare bench/results/reference-before-clone3 bench/results/reference`.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `cell(text)` | any value | text safe for a Markdown cell (`\|` escaped, newlines removed, `None` becomes empty) | – |
| `table(header, rows)` | list of column names, list of row lists | Markdown table string | – |
| `mib(value_bytes, divisor=1)` | bytes (text or number), optional divisor (k) | `bytes / divisor / 1048576` with 2 decimals, or `-` if missing | – |
| `startup_table(rows)` | rows of `startup_summary.csv` | table, one row per configuration in file order (including `minibox_limits_migrate`); a row whose `status` is not `measured` shows its note | – |
| `memory_table(rows)` | rows of `memory.csv` | table of cgroup, init PSS, monitor PSS (each total divided by k), total / k in MiB and bytes; rows without `k` show the note | – |
| `incremental_table(incr)` | rows of `memory_incremental.csv` | table of bytes and MiB per extra container | – |
| `limits_table(rows)` | rows of `limits.csv` | table check / expected / measured / status | – |
| `machine_lines(system)` | dict from `system.json` | Markdown list: date, kernel, CPU model and cores, RAM, WSL, runs/warm-ups/seed, minibox build, Docker (not measured and why) | – |
| `compare_table(before_rows, after_rows)` | rows of the two `startup_summary.csv` files | Markdown table in the order of `COMPARE_ROWS`; missing or unmeasured configurations become `-` rows | – |
| `compare_report(before_dir, after_dir)` | two directories | heading plus the compare table | – |
| `load_system(results_dir)` | path | dict, `{}` if `system.json` is missing | `json.load` raises on invalid JSON |
| `report(results_dir)` | path | the whole output as a string | – |
| `main(argv)` | argument list (`--compare B A` or one directory) | exit status | message on stderr if a directory is missing, has no `startup_summary.csv`, or the compare arguments are wrong |

It reuses `read_csv` and `num` from `bench/make_charts.py`.

## System calls used and why
Only file reads (`open`, `read`) through Python, and `write` to stdout.

## OS concept
None; it presents measurements. What the numbers mean: `docs/BENCHMARKS.md` (Analysis).

## How to test
`python3 -m unittest tests/test_bench_stats.py -v` (class `ReportTableTests`: values copied exactly, division by k, escaping, machine lines, bad directory; class `CompareTests`: row order, values taken from the right directory, a missing row printed as dashes, bad arguments). Real data: `python3 bench/make_report_tables.py bench/results/reference`.
