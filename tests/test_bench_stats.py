"""test_bench_stats.py - unit tests for bench/stats.py, bench/make_charts.py and bench/make_report_tables.py (no root needed).

Run: python3 -m unittest tests/test_bench_stats.py -v     (from the repository root)
The chart tests use a tiny fixture written to a temp directory; the numbers in it are test data
only, not benchmark results.
"""
import math
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench"))
import bench  # noqa: E402
import make_charts  # noqa: E402
import make_report_tables  # noqa: E402
import stats  # noqa: E402

STARTUP = """config,status,n,failed,min_ms,median_ms,mean_ms,p95_ms,max_ms,stdev_ms,note
host_true,measured,5,0,1.0,1.5,1.6,2.0,2.1,0.4,host
minibox_nolimits,measured,5,0,20.0,25.0,26.0,31.0,32.0,4.0,mb
minibox_limits,measured,5,0,3.5,4.1,4.3,5.0,5.2,0.6,clone3
minibox_limits_migrate,measured,5,0,3.6,32.1,22.0,33.8,64.5,16.0,migrate
minibox_veth_limits,measured,5,0,114.5,165.4,150.8,194.3,217.8,30.3,veth
unshare_ns,measured,5,0,3.5,3.9,4.4,8.4,10.0,1.6,unshare
docker,not measured,0,0,,,,,,,not measured: docker CLI not found
"""
MEMORY = """config,k,cgroup_memory_current_total,init_mem_total,monitor_or_shim_mem_total,total_bytes,per_container_bytes,note
minibox,1,1000000,200000,300000,1300000,1300000,pss
minibox,5,5000000,1000000,1500000,6500000,1300000,pss
minibox,10,10000000,2000000,3000000,13000000,1300000,pss
host_sleep,1,,100000,,100000,100000,pss
docker,,,,,,,not measured: docker CLI not found
"""
INCR = "config,incremental_bytes_per_container\nminibox,1300000\n"
LIMITS = """test,expected,measured,tolerance,status,detail
memory/exit_code,137,137,exact,OK,
cpu/ratio_at_0.5,-,-,-,TIMEOUT,no result within 20 s
pids/peak,-,-,-,ERROR,boom <x>
"evil <b>&""name",a < b,"x & y",-,FAIL,
"""


class StatsTests(unittest.TestCase):
    def test_median(self):
        self.assertEqual(stats.median([3, 1, 2]), 2)
        self.assertEqual(stats.median([4, 1, 3, 2]), 2.5)
        self.assertIsNone(stats.median([]))

    def test_percentile_and_p95(self):
        self.assertEqual(stats.percentile([1, 2, 3, 4, 5], 0), 1)
        self.assertEqual(stats.percentile([1, 2, 3, 4, 5], 100), 5)
        self.assertEqual(stats.percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(stats.p95(list(range(1, 101))), 95.05)
        self.assertEqual(stats.p95([7]), 7)
        self.assertIsNone(stats.p95([]))
        with self.assertRaises(ValueError):
            stats.percentile([1], 101)

    def test_stdev(self):
        self.assertAlmostEqual(stats.stdev([2, 4, 4, 4, 5, 5, 7, 9]), math.sqrt(32 / 7))
        self.assertEqual(stats.stdev([5]), 0.0)
        self.assertIsNone(stats.stdev([]))

    def test_summarise(self):
        s = stats.summarise([1, 2, 3, 4])
        self.assertEqual(s["n"], 4)
        self.assertEqual((s["min"], s["max"], s["median"], s["mean"]), (1, 4, 2.5, 2.5))
        self.assertEqual(set(s), set(stats.SUMMARY_KEYS))

    def test_summarise_empty(self):
        s = stats.summarise([])
        self.assertEqual(s["n"], 0)
        self.assertIsNone(s["median"])

    def test_incremental_cost(self):
        self.assertAlmostEqual(stats.incremental_cost([(1, 150), (5, 550), (10, 1050)]), 100.0)
        self.assertIsNone(stats.incremental_cost([(1, 5)]))


class StartupConfigTests(unittest.TestCase):
    """The clone3 before/after rows of the startup benchmark (no root needed: argv only)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        args = bench.parse_args(["startup", "--quick", "--out", self.tmp.name])
        self.b = bench.Bench(args)

    def tearDown(self):
        self.tmp.cleanup()
        shutil.rmtree(self.b.state_dir, ignore_errors=True)

    def test_migrate_row_follows_limits_row_with_the_same_command(self):
        cfgs = bench.startup_configs(self.b)
        names = list(cfgs)
        self.assertEqual(names.index("minibox_limits_migrate"), names.index("minibox_limits") + 1)
        self.assertEqual(cfgs["minibox_limits"][0], cfgs["minibox_limits_migrate"][0])
        self.assertIn("MINIBOX_NO_CLONE3", cfgs["minibox_limits_migrate"][1])
        self.assertEqual(len(names), 7)

    def test_only_the_migrate_row_gets_the_switch(self):
        base = {"PATH": "/bin", "MINIBOX_STATE_DIR": "/x"}
        self.assertEqual(bench.startup_env(base, "minibox_limits_migrate")["MINIBOX_NO_CLONE3"], "1")
        for name in ("minibox_limits", "minibox_nolimits", "minibox_veth_limits", "host_true"):
            self.assertEqual(bench.startup_env(base, name), base)
        self.assertNotIn("MINIBOX_NO_CLONE3", base)         # the base environment is not modified


class WorkloadTests(unittest.TestCase):
    """Workloads run as `sh -c STRING`: busybox sh execs the last command, which would then be the
    container's PID 1 and could not be killed by signals from inside (see docs/VIVA_NOTES.md)."""

    def test_every_workload_keeps_the_shell_as_pid1(self):
        for w in bench.all_workloads():
            self.assertTrue(w.rstrip().endswith("; exit 0"), w)

    def test_cpu_workload_uses_seconds(self):
        w = bench.cpu_workload(7)
        self.assertTrue(w.startswith("timeout 7 sh -c "))
        self.assertTrue(w.endswith("; exit 0"))

    def test_pids_workload_counts_children(self):
        self.assertIn("-lt 33 ", bench.pids_workload(33))

    def test_exit_0_is_outside_quotes(self):
        # the trailing "; exit 0" must not be inside a single-quoted awk / sh -c program
        for w in bench.all_workloads():
            self.assertEqual(w.count("'") % 2, 0, w)
            self.assertEqual(w.rsplit("; exit 0", 1)[0].count("'") % 2, 0, w)


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        for name, text in (("startup_summary.csv", STARTUP), ("memory.csv", MEMORY),
                           ("memory_incremental.csv", INCR), ("limits.csv", LIMITS)):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_charts_written_and_well_formed(self):
        out = os.path.join(self.dir, "charts")
        paths = make_charts.make_all(self.dir, out)
        self.assertEqual(sorted(os.path.basename(p) for p in paths),
                         ["limits.svg", "memory.svg", "startup.svg"])
        for p in paths:
            ET.parse(p)             # raises ParseError if not well-formed XML

    def test_missing_docker_is_noted(self):
        svg = make_charts.startup_svg(make_charts.read_csv(os.path.join(self.dir, "startup_summary.csv")))
        self.assertIn("not measured", svg)
        self.assertIn("docker CLI not found", svg)
        ET.fromstring(svg.split("?>", 1)[1])
        mem = make_charts.memory_svg(make_charts.read_csv(os.path.join(self.dir, "memory.csv")), [])
        self.assertIn("Not measured - docker", mem)

    def test_text_is_escaped(self):
        svg = make_charts.limits_svg(make_charts.read_csv(os.path.join(self.dir, "limits.csv")))
        self.assertNotIn("<b>", svg)
        root = ET.fromstring(svg.split("?>", 1)[1])
        texts = [e.text for e in root.iter("{http://www.w3.org/2000/svg}text")]
        self.assertIn('evil <b>&"name', texts)
        for word in ("OK", "FAIL", "TIMEOUT", "ERROR"):
            self.assertTrue(any(word in (x or "") for x in texts), word)
        self.assertTrue(any("ERROR/TIMEOUT" in (x or "") for x in texts))

    def test_missing_csv_is_skipped(self):
        os.remove(os.path.join(self.dir, "memory.csv"))
        paths = make_charts.make_all(self.dir, os.path.join(self.dir, "o"))
        self.assertEqual(len(paths), 2)

    def test_log_ticks_cover_data_and_stop_one_tick_above(self):
        self.assertEqual(make_charts.log_ticks(1.7, 194.3), [1, 2, 5, 10, 20, 50, 100, 200])
        self.assertEqual(make_charts.log_ticks(3.9, 8.4), [2, 5, 10])

    def test_linear_ticks_fit_data(self):
        ticks = make_charts.linear_ticks(5.01)
        self.assertEqual(ticks[0], 0)
        self.assertGreaterEqual(ticks[-1], 5.01)
        self.assertLess(ticks[-1], 7)

    def test_bar_label_text(self):
        self.assertEqual(make_charts.bar_label(3.94, 8.42), "3.9 ms (p95 8.4)")

    def test_startup_axis_is_log_and_labelled(self):
        svg = make_charts.startup_svg(make_charts.read_csv(os.path.join(self.dir, "startup_summary.csv")))
        self.assertIn("log scale", svg)
        self.assertIn("1.5 ms (p95 2.0)", svg)

    def test_seven_startup_labels_do_not_overlap_and_fit(self):
        rows = make_charts.read_csv(os.path.join(self.dir, "startup_summary.csv"))
        self.assertEqual(len(rows), 7)
        svg = make_charts.startup_svg(rows)
        root = ET.fromstring(svg.split("?>", 1)[1])
        ns = "{http://www.w3.org/2000/svg}"
        width = float(root.get("width"))
        by_row = {}
        for e in root.iter(ns + "text"):
            if e.get("class") not in ("tick", "lbl") or e.get("text-anchor") != "middle":
                continue
            x, y = float(e.get("x")), float(e.get("y"))
            half = len(e.text) * make_charts.CHAR_W / 2
            self.assertGreaterEqual(x - half, 0, e.text)
            self.assertLessEqual(x + half, width, e.text)
            by_row.setdefault(round(y), []).append((x - half, x + half, e.text))
        self.assertTrue(by_row)
        for spans in by_row.values():
            spans.sort()
            for (_, end, a), (start, _, b) in zip(spans, spans[1:]):
                self.assertLess(end, start, "%s overlaps %s" % (a, b))
        self.assertIn("minibox limits", svg)
        self.assertIn("migrate", svg)

    def test_wrap_label_and_n_summary(self):
        self.assertEqual(make_charts.wrap_label("minibox_limits_migrate"), ["minibox limits", "migrate"])
        self.assertEqual(make_charts.wrap_label("minibox_veth_limits"), ["minibox veth", "limits"])
        self.assertEqual(make_charts.wrap_label("docker"), ["docker"])
        same = [{"config": "a", "n": "30"}, {"config": "b", "n": "30"}]
        self.assertEqual(make_charts.n_summary(same), "n=30 per configuration")
        self.assertEqual(make_charts.n_summary([{"config": "a", "n": "5"}, {"config": "b", "n": "4"}]),
                         "a n=5, b n=4")

    def test_legend_entries_do_not_overlap(self):
        labels = ["minibox cgroup memory.current", "minibox init PSS", "minibox monitor PSS",
                  "host sleep PSS (dashed line)"]
        lay = make_charts.legend_layout(labels, 80, 400, 100)
        self.assertGreater(len({y for _, y, _, _ in lay}), 1)       # narrow width wraps to rows
        for i, (x, y, _tx, end) in enumerate(lay):
            self.assertLessEqual(end, 80 + 400 + 1)
            for x2, y2, _t2, end2 in lay[i + 1:]:
                if y == y2:
                    self.assertTrue(end < x2 or end2 < x, "overlap")

    def test_nice_max(self):
        self.assertEqual(make_charts.nice_max(0.3), 0.5)
        self.assertEqual(make_charts.nice_max(7), 10)
        self.assertEqual(make_charts.nice_max(120), 200)


class ReportTableTests(unittest.TestCase):
    """make_report_tables on the tiny fixtures above (test data, not benchmark results)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        for name, text in (("startup_summary.csv", STARTUP), ("memory.csv", MEMORY),
                           ("memory_incremental.csv", INCR), ("limits.csv", LIMITS)):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write(text)
        with open(os.path.join(self.dir, "system.json"), "w") as f:
            f.write('{"date": "D", "kernel": "K", "cpu_model": "C", "cpu_cores": 4, "ram_kb": 1048576,'
                    ' "wsl": true, "docker": null, "docker_note": "docker CLI not found"}')

    def tearDown(self):
        self.tmp.cleanup()

    def test_startup_table_has_the_migrate_row(self):
        out = make_report_tables.startup_table(make_charts.read_csv(os.path.join(self.dir, "startup_summary.csv")))
        self.assertIn("| minibox_limits | 5 | 3.5 | 4.1 | 4.3 | 5.0 | 0.6 |", out)
        self.assertIn("| minibox_limits_migrate | 5 | 3.6 | 32.1 | 22.0 | 33.8 | 16.0 |", out)

    def test_startup_table_copies_file_values(self):
        out = make_report_tables.startup_table(make_charts.read_csv(os.path.join(self.dir, "startup_summary.csv")))
        self.assertIn("| minibox_nolimits | 5 | 20.0 | 25.0 | 26.0 | 31.0 | 4.0 |", out)
        self.assertIn("| docker | not measured: docker CLI not found |", out)

    def test_memory_table_divides_by_k(self):
        out = make_report_tables.memory_table(make_charts.read_csv(os.path.join(self.dir, "memory.csv")))
        # k=5: cgroup 5000000 B / 5 / 1048576 = 0.95 MiB; init 1000000 / 5 = 0.19 MiB
        self.assertIn("| minibox | 5 | 0.95 | 0.19 | 0.29 | 1.24 | 1300000 |", out)
        self.assertIn("| host_sleep | 1 | - | 0.10 | - | 0.10 | 100000 |", out)
        self.assertIn("not measured: docker CLI not found", out)

    def test_incremental_and_limits(self):
        inc = make_report_tables.incremental_table(make_charts.read_csv(os.path.join(self.dir, "memory_incremental.csv")))
        self.assertIn("| minibox | 1300000 | 1.24 |", inc)
        lim = make_report_tables.limits_table(make_charts.read_csv(os.path.join(self.dir, "limits.csv")))
        self.assertIn("| memory/exit_code | 137 | 137 | OK |", lim)
        self.assertIn("| cpu/ratio_at_0.5 | - | - | TIMEOUT |", lim)

    def test_pipe_in_text_is_escaped(self):
        self.assertEqual(make_report_tables.cell("a|b"), "a\\|b")

    def test_machine_lines_and_full_report(self):
        out = make_report_tables.report(self.dir)
        self.assertIn("- Kernel: K", out)
        self.assertIn("- CPU: C, 4 cores", out)
        self.assertIn("- RAM: 1048576 kB (1.00 GiB)", out)
        self.assertIn("- Docker: not measured (docker CLI not found)", out)
        for heading in ("### Startup time", "### Memory per container", "### Limit enforcement"):
            self.assertIn(heading, out)

    def test_main_rejects_missing_directory(self):
        self.assertEqual(make_report_tables.main(["x", os.path.join(self.dir, "nope")]), 1)


if __name__ == "__main__":
    unittest.main()
