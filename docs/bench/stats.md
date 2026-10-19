# bench/stats.py

## Purpose
Pure statistics helpers for the benchmarks. No root, no subprocess, no file access, so `tests/test_bench_stats.py` can test them with known numbers.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `mean(values)` | list of numbers | arithmetic mean, `None` if empty | – |
| `median(values)` | list | middle value (mean of the two middle values for an even count), `None` if empty | – |
| `percentile(values, p)` | list, `p` in 0..100 | p-th percentile, linear interpolation at rank `p/100*(n-1)`, `None` if empty | `ValueError` if `p` is outside 0..100 |
| `p95(values)` | list | `percentile(values, 95)` | – |
| `stdev(values)` | list | sample standard deviation (divides by n-1); `0.0` for one value, `None` if empty | – |
| `summarise(values)` | iterable | dict with keys `n, min, median, mean, p95, max, stdev` (`SUMMARY_KEYS`); empty input gives `n=0` and `None` fields | – |
| `incremental_cost(points)` | list of `(count, total)` pairs | least-squares slope of total versus count (extra bytes per extra container); `None` with fewer than two different counts | – |

## System calls used and why
None (only `math`).

## OS concept
Not an OS concept itself; it is the statistics used to report timing data. The median and p95 are used because timings have a long slow tail (scheduler, page cache), so the mean alone misleads.

## How to test
`python3 -m unittest tests/test_bench_stats.py -v` (class `StatsTests`).
