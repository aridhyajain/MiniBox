#!/usr/bin/env python3
"""make_report_tables.py - print Markdown tables for the report from a bench results directory.

Usage: python3 bench/make_report_tables.py [RESULTS_DIR]     (default: bench/results/reference)
       python3 bench/make_report_tables.py --compare BEFORE_DIR AFTER_DIR
Standard library only, no root. Every number printed comes from the CSV / JSON files in RESULTS_DIR;
values are copied as text from the files, except memory sizes, which are converted from bytes to MiB.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_charts import num, read_csv  # noqa: E402

MIB = 1048576.0


def cell(text):
    """Make a value safe inside a Markdown table cell."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def table(header, rows):
    """Markdown table from a header list and a list of row lists."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def mib(value_bytes, divisor=1):
    """Bytes (CSV text or number) divided by `divisor`, as MiB with 2 decimals; '-' when missing."""
    v = num(value_bytes)
    return "-" if v is None else "%.2f" % (v / divisor / MIB)


def startup_table(rows):
    """One row per configuration; a configuration that was not measured says so."""
    out = []
    for r in rows:
        if r.get("status") != "measured":
            out.append([r["config"], r.get("note") or "not measured", "-", "-", "-", "-", "-"])
            continue
        out.append([r["config"], r["n"], r["min_ms"], r["median_ms"], r["mean_ms"], r["p95_ms"], r["stdev_ms"]])
    return table(["Configuration", "n", "min (ms)", "median (ms)", "mean (ms)", "p95 (ms)", "stdev (ms)"], out)


def memory_table(rows):
    """Per-container memory: cgroup, init PSS and monitor PSS (each total / k), their sum, and the host sleep."""
    out = []
    for r in rows:
        if num(r.get("k")) is None:
            out.append([r["config"], "-", "-", "-", "-", "-", r.get("note") or "not measured"])
            continue
        k = int(r["k"])
        out.append([r["config"], k, mib(r["cgroup_memory_current_total"], k), mib(r["init_mem_total"], k),
                    mib(r["monitor_or_shim_mem_total"], k), mib(r["per_container_bytes"]),
                    r["per_container_bytes"]])
    return table(["Configuration", "k", "cgroup memory.current / k (MiB)", "init PSS / k (MiB)",
                  "monitor PSS / k (MiB)", "total / k (MiB)", "total / k (bytes)"], out)


def incremental_table(incr):
    """Least-squares slope of total memory versus k: the cost of one extra container."""
    out = [[r["config"], r["incremental_bytes_per_container"], mib(r["incremental_bytes_per_container"])]
           for r in incr]
    return table(["Configuration", "extra cost per container (bytes)", "(MiB)"], out)


def limits_table(rows):
    """Expected, measured and status of every limit check."""
    return table(["Check", "Expected", "Measured", "Status"],
                 [[r["test"], r["expected"], r["measured"], r["status"]] for r in rows])


def machine_lines(system):
    """The machine description from system.json, as a Markdown list."""
    ram = num(system.get("ram_kb"))
    ram_text = "%d kB (%.2f GiB)" % (ram, ram / MIB) if ram is not None else "unknown"
    wsl = system.get("wsl")
    return "\n".join([
        "- Run date: %s" % system.get("date"),
        "- Kernel: %s" % system.get("kernel"),
        "- CPU: %s, %s cores" % (system.get("cpu_model"), system.get("cpu_cores")),
        "- RAM: %s" % ram_text,
        "- WSL: %s" % ("yes" if wsl else "no"),
        "- Runs per configuration: %s, warm-ups: %s, CPU busy loop: %s s, shuffle seed: %s" % (
            system.get("runs_per_config"), system.get("warmups"), system.get("cpu_busy_seconds"),
            system.get("shuffle_seed")),
        "- minibox build: %s" % system.get("minibox_version"),
        "- Docker: %s" % ("not measured (%s)" % system.get("docker_note") if system.get("docker") is None
                          else system.get("docker")),
    ])


def load_system(results_dir):
    path = os.path.join(results_dir, "system.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def report(results_dir):
    """The whole report fragment as one string."""
    p = lambda name: os.path.join(results_dir, name)  # noqa: E731
    parts = ["### Machine and run\n\n" + machine_lines(load_system(results_dir)),
             "### Startup time (ms, wall clock for `/bin/true`)\n\n" + startup_table(read_csv(p("startup_summary.csv"))),
             "### Memory per container (`sleep 300`)\n\n" + memory_table(read_csv(p("memory.csv"))),
             "### Incremental memory cost per extra container\n\n" + incremental_table(read_csv(p("memory_incremental.csv"))),
             "### Limit enforcement\n\n" + limits_table(read_csv(p("limits.csv")))]
    return "\n\n".join(parts) + "\n"


# (label in the table, which directory, config name in startup_summary.csv)
COMPARE_ROWS = [
    ("minibox_nolimits", "before", "minibox_nolimits"),
    ("minibox_limits", "before", "minibox_limits"),
    ("minibox_nolimits", "after", "minibox_nolimits"),
    ("minibox_limits", "after", "minibox_limits"),
    ("minibox_limits_migrate", "after", "minibox_limits_migrate"),
    ("minibox_veth_limits", "before", "minibox_veth_limits"),
    ("minibox_veth_limits", "after", "minibox_veth_limits"),
]


def compare_table(before_rows, after_rows):
    """Before/after startup table. Values are copied as text from the two startup_summary.csv files;
    a configuration missing from a file (or not measured) gets a row of '-' so nothing is invented."""
    by_dir = {"before": {r["config"]: r for r in before_rows}, "after": {r["config"]: r for r in after_rows}}
    out = []
    for label, which, config in COMPARE_ROWS:
        r = by_dir[which].get(config)
        if r is None or r.get("status") != "measured":
            out.append([label, which, "-", "-", "-", "-", "-"])
        else:
            out.append([label, which, r["n"], r["median_ms"], r["mean_ms"], r["p95_ms"], r["max_ms"]])
    return table(["Configuration", "Run", "n", "median (ms)", "mean (ms)", "p95 (ms)", "max (ms)"], out)


def compare_report(before_dir, after_dir):
    """The compare output as one string."""
    p = lambda d: os.path.join(d, "startup_summary.csv")  # noqa: E731
    return ("### Startup time, before and after the clone3 change (ms, wall clock for `/bin/true`)\n\n"
            + compare_table(read_csv(p(before_dir)), read_csv(p(after_dir))) + "\n")


def main(argv):
    if len(argv) > 1 and argv[1] == "--compare":
        if len(argv) != 4:
            print("usage: make_report_tables.py --compare BEFORE_DIR AFTER_DIR", file=sys.stderr)
            return 1
        for d in argv[2:4]:
            if not os.path.isdir(d):
                print("error: %s is not a directory" % d, file=sys.stderr)
                return 1
            if not os.path.exists(os.path.join(d, "startup_summary.csv")):
                print("error: %s has no startup_summary.csv" % d, file=sys.stderr)
                return 1
        sys.stdout.write(compare_report(argv[2], argv[3]))
        return 0
    results_dir = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench", "results", "reference")
    if not os.path.isdir(results_dir):
        print("error: %s is not a directory" % results_dir, file=sys.stderr)
        return 1
    sys.stdout.write(report(results_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
