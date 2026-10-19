# bench/make_charts.py

## Purpose
Turns a results directory written by `bench.py` into three SVG charts, with the standard library only (no matplotlib, no pip, works offline, no root):
`startup.svg` (bar = median, whisker to p95, per configuration, log-scale y axis), `memory.svg` (memory per container for 1/5/10 containers), `limits.svg` (table of
expected vs measured with the status `OK`/`FAIL`/`ERROR`/`TIMEOUT` as text and a mark: ✓, ✗, ⚠; the subtitle counts ERROR/TIMEOUT rows. Old CSVs with a `result` column are still read). Colours are CSS variables with a `prefers-color-scheme: dark` override.
A configuration that was not measured (for example docker) gets one footnote line in the chart, `Not measured: docker (docker CLI not found)` (see `not_measured_note`). All CSV text goes through `xml.sax.saxutils.escape`.

## Usage
`python3 bench/make_charts.py [RESULTS_DIR] [--out DIR]` (default `bench/results/latest`; charts are written next to the CSVs unless `--out`). Exit 0 if at least one chart was written, 1 otherwise.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `read_csv(path)` | path | list of dict rows, `[]` if the file does not exist | – |
| `num(s)` | string | float or `None` | – |
| `t(x, y, text, cls, anchor, extra)` | position, text, CSS class | one escaped `<text>` element | – |
| `wrap(width, height, title, subtitle, body)` | sizes, texts, SVG body | the full SVG document string | – |
| `nice_max(v)` | number | `v` rounded up to 1/2/5 x 10^k for a clean axis end | – |
| `linear_ticks(vmax_data)` | largest value in the chart | round ticks from 0 to just above the data (at most 6 steps of 1/2/5 x 10^k) | – |
| `log_ticks(lo, hi)` | smallest bar, largest p95 | 1-2-5 ticks from the one at/below `lo` to the first at/above `hi` | – |
| `fmt_tick(v)` | number | tick label text | – |
| `y_axis(left, top, w, h, ticks, unit, log=False)` | geometry, ticks, axis title | `(svg lines, pos)`; `pos(v)` gives the y coordinate of a value (log or linear) | – |
| `bar_label(med, p95)` | ms values | `"3.9 ms (p95 8.4)"` | – |
| `legend_layout(items, left, width, y0)` | legend labels | `(x, y, text_x, text_end)` per entry; wraps to a new row so entries never overlap | – |
| `wrap_label(name, max_chars=16)` | configuration name | list of lines (split at underscores) | – |
| `n_summary(measured)` | measured rows | `"n=30 per configuration"` when all `n` are equal, else one `name n=..` per row | – |
| `not_measured_note(config, note)` | configuration name, note text from the CSV (may be empty or `None`) | `"Not measured: <config> (<reason>)"`, or `"Not measured: <config>"` without a reason; a leading `not measured:` or `<config>:` in the note is removed so nothing is repeated | – |
| `startup_svg(rows)` | rows of `startup_summary.csv` | SVG string: **log** y axis (ticks cover the data and stop one tick above the largest p95; "log scale" in subtitle and axis title), one label above each bar, whisker to p95, at least `MIN_STARTUP_SLOT` (150 px) per bar so 7 configurations do not overlap (the SVG grows wider), two-line category labels, "not measured" slot | – |
| `memory_svg(rows, incr)` | rows of `memory.csv`, rows of `memory_incremental.csv` | SVG string (y axis max chosen from the data incl. the host line; wrapped legend; minibox cgroup / init PSS / monitor PSS bars, host sleep dashed line, docker note) | – |
| `limits_svg(rows)` | rows of `limits.csv` | SVG table string | – |
| `make_all(results_dir, out_dir)` | two paths | list of written paths | creates `out_dir`; prints "skipped" for a missing CSV |
| `main(argv)` | argument list | exit status | message if the directory does not exist |

## System calls used and why
Only file I/O (`open`, `write`, `mkdir`) through Python.

## OS concept
Not an OS concept; it presents the measurements. Reading the charts: see `docs/BENCHMARKS.md`.

## How to test
`python3 -m unittest tests/test_bench_stats.py -v` (class `ChartTests`: all three files are well-formed XML, missing docker is noted once (`test_not_measured_note_text` checks the wording), special characters are escaped, a missing CSV is skipped). `tests/phase6_test.sh` checks the charts on real data.
