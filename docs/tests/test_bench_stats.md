# tests/test_bench_stats.py

## Purpose
Unit tests (standard `unittest`, no root) for `bench/stats.py`, `bench/make_charts.py` and `bench/make_report_tables.py`. The chart tests write a tiny fixture (test data, not benchmark results) with a missing docker configuration and a test name containing `<`, `&` and `"`.

## Tests
| Class / test | Checks |
|--------------|--------|
| `StatsTests.test_median`, `test_percentile_and_p95`, `test_stdev`, `test_summarise`, `test_summarise_empty`, `test_incremental_cost` | known values, edge cases (empty list, one value, bad percentile) |
| `WorkloadTests.test_every_workload_keeps_the_shell_as_pid1` | every workload string ends with `; exit 0`, so the shell, not the last command, is the container's PID 1 |
| `WorkloadTests.test_cpu_workload_uses_seconds`, `test_pids_workload_counts_children`, `test_exit_0_is_outside_quotes` | parameters appear in the string; the trailing exit is outside any quoted program |
| `ChartTests.test_all_charts_written_and_well_formed` | three SVGs written and parsed with `xml.etree` |
| `ChartTests.test_missing_docker_is_noted` | "not measured" and the reason appear in the startup and memory charts |
| `ChartTests.test_text_is_escaped` | special characters survive as text and no raw `<b>` appears; OK, FAIL, ERROR and TIMEOUT rows all rendered (the fixture has one of each; the subtitle counts ERROR/TIMEOUT) |
| `ChartTests.test_missing_csv_is_skipped` | a missing CSV skips that chart only |
| `ChartTests.test_nice_max` | axis rounding |
| `ChartTests.test_log_ticks_cover_data_and_stop_one_tick_above`, `test_linear_ticks_fit_data`, `test_bar_label_text`, `test_startup_axis_is_log_and_labelled`, `test_legend_entries_do_not_overlap` | axis ticks, label text, "log scale" wording on the startup chart, legend layout |
| `ReportTableTests.test_startup_table_copies_file_values` | CSV values appear unchanged in the startup table; docker row says "not measured" |
| `ReportTableTests.test_memory_table_divides_by_k` | per-container MiB = total / k / 1048576 (hand-checked fixture values); host row and docker note |
| `ReportTableTests.test_incremental_and_limits` | incremental and limits tables, TIMEOUT row kept |
| `ReportTableTests.test_pipe_in_text_is_escaped`, `test_machine_lines_and_full_report`, `test_main_rejects_missing_directory` | `\|` escaping; kernel/CPU/RAM/Docker lines and all sections; exit 1 for a missing directory |

## System calls used and why
Only file I/O in a temp directory.

## OS concept
None; it tests the reporting code.

## How to test
`python3 -m unittest tests/test_bench_stats.py -v` from the repository root.
