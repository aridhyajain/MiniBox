"""stats.py - pure statistics helpers for the minibox benchmarks.

No root, no subprocess, no I/O: every function takes numbers and returns numbers, so the
unit tests (tests/test_bench_stats.py) can check them with known inputs.
"""
import math


def mean(values):
    """Arithmetic mean, or None for an empty list."""
    if not values:
        return None
    return sum(values) / len(values)


def median(values):
    """Middle value (average of the two middle values for an even count), or None if empty."""
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def percentile(values, p):
    """p-th percentile (0-100) with linear interpolation between ranks, or None if empty.

    Same definition as numpy's default: rank = p/100 * (n-1).
    """
    if not values:
        return None
    if not 0 <= p <= 100:
        raise ValueError("percentile must be between 0 and 100")
    s = sorted(values)
    rank = p / 100.0 * (len(s) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(s[low])
    return s[low] + (s[high] - s[low]) * (rank - low)


def p95(values):
    """95th percentile."""
    return percentile(values, 95)


def stdev(values):
    """Sample standard deviation (n-1). 0.0 for one value, None for an empty list."""
    if not values:
        return None
    if len(values) == 1:
        return 0.0
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


SUMMARY_KEYS = ("n", "min", "median", "mean", "p95", "max", "stdev")


def summarise(values):
    """Dict with n, min, median, mean, p95, max, stdev. Empty input gives n=0 and None fields."""
    values = list(values)
    if not values:
        return {k: (0 if k == "n" else None) for k in SUMMARY_KEYS}
    return {
        "n": len(values),
        "min": float(min(values)),
        "median": median(values),
        "mean": mean(values),
        "p95": p95(values),
        "max": float(max(values)),
        "stdev": stdev(values),
    }


def incremental_cost(points):
    """Least-squares slope of total versus count: the extra memory of one more container.

    points is a list of (count, total) pairs. Needs at least two different counts, else None.
    """
    if len({c for c, _ in points}) < 2:
        return None
    n = len(points)
    mx = sum(c for c, _ in points) / n
    my = sum(t for _, t in points) / n
    sxx = sum((c - mx) ** 2 for c, _ in points)
    sxy = sum((c - mx) * (t - my) for c, t in points)
    return sxy / sxx
