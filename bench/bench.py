#!/usr/bin/env python3
"""bench.py - minibox benchmarks: startup time, memory overhead, limit enforcement.

Usage (needs root, because minibox does):
    sudo python3 bench/bench.py all [--quick]
    sudo python3 bench/bench.py startup|memory|limits [--quick] [--runs N] [--out DIR]

Results go to <out>/<timestamp>/ (default bench/results/) and are copied to <out>/latest/.
Standard library only. See docs/BENCHMARKS.md for the method and docs/bench/bench.md for the code.
Every number written comes from a measurement made by this script; nothing is hard-coded.
"""
import argparse
import csv
import datetime
import glob
import hashlib
import json
import os
import platform
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stats  # noqa: E402

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CG_BASE = "/sys/fs/cgroup/minibox"
MB_STATE_KEYS = ("pid", "monitor_pid", "status")
MEM_COUNTS = (1, 5, 10)
LIMITS_FOR_STARTUP = ["--memory", "256M", "--cpus", "1", "--pids", "64"]
# Startup configurations that need an extra environment variable (only for that configuration).
# MINIBOX_NO_CLONE3=1 makes minibox use clone() + a write to cgroup.procs (a cgroup migration)
# instead of clone3(CLONE_INTO_CGROUP): the "before" of the clone3 fix, next to the normal "after".
STARTUP_EXTRA_ENV = {"minibox_limits_migrate": {"MINIBOX_NO_CLONE3": "1"}}
DOCKER_IMAGE = "alpine"
CALL_TIMEOUT = 20           # seconds for any single minibox/docker call unless a step needs more
TIMEOUT_RC = -999           # returncode recorded for a call that hit its timeout


# ---------------------------------------------------------------- workloads
# Small pure functions that build the `sh -c` strings run inside containers, so they can be unit
# tested. RULE: the last command must never be exec'd as the container's PID 1. busybox sh execs
# the final command of `sh -c "..."`; a PID-namespace init ignores signals from inside its own
# namespace unless it installed a handler, so a program that ends by signalling itself (timeout)
# or that needs a signal to stop would hang the container forever. Ending with `; exit 0` keeps
# the shell as PID 1. (The runtime is correct; see docs/VIVA_NOTES.md, Phase 6.)
KEEP_SHELL = "; exit 0"


def memory_hog_workload():
    """Doubles a string forever: must be OOM-killed by the memory limit."""
    return "awk 'BEGIN{s=\"x\"; while(1) s=s s}'" + KEEP_SHELL


def memory_control_workload():
    """Bounded version (2^27 characters, about 128 MiB): needs more than 64M but finishes."""
    return "awk 'BEGIN{s=\"x\"; for(i=0;i<27;i++) s=s s; print length(s)}'" + KEEP_SHELL


def pids_workload(children=60):
    """Starts `children` background sleeps; prints started_all only if every fork worked."""
    return ("i=0; while [ $i -lt %d ]; do sleep 5 & i=$((i+1)); done; echo started_all" % children
            + KEEP_SHELL)


def cpu_workload(seconds):
    """Busy loop killed by timeout after `seconds`; the shell stays PID 1 to see timeout finish."""
    return "timeout %d sh -c 'while :; do :; done'" % seconds + KEEP_SHELL


def all_workloads():
    """Every workload string run with `sh -c` inside a container (used by the unit test)."""
    return [memory_hog_workload(), memory_control_workload(), pids_workload(), cpu_workload(5)]


class Bench:
    """Holds the paths, options and the list of things to clean up (no module-level state)."""

    def __init__(self, args):
        self.args = args
        self.quick = args.quick
        self.runs = args.runs if args.runs else (5 if args.quick else 30)
        self.warmups = 1 if args.quick else 3
        self.cpu_secs = 2 if args.quick else 5
        self.seed = args.seed if args.seed is not None else int(time.time())
        self.mb = os.path.join(ROOT_DIR, "minibox")
        self.rootfs = os.path.join(ROOT_DIR, "rootfs")
        self.state_dir = tempfile.mkdtemp(prefix="minibox-bench-")
        self.env = dict(os.environ, MINIBOX_STATE_DIR=self.state_dir)
        self.stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.base_out = os.path.abspath(args.out)
        self.run_dir = os.path.join(self.base_out, self.stamp)
        self.lines = []             # everything printed, saved as summary.txt
        self.docker_names = []      # docker containers started by us
        self.host_procs = []        # host sleep processes started by us
        self.docker_ok = False
        self.docker_note = ""
        self.errors = []            # ERROR/TIMEOUT events; make the exit code non-zero
        self.limit_rows = []        # rows of limits.csv, flushed after every check

    # ---------------------------------------------------------------- output
    def say(self, line=""):
        print(line, flush=True)
        self.lines.append(line)

    def write_csv(self, name, header, rows):
        path = os.path.join(self.run_dir, name)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        return path

    # ------------------------------------------------------------- minibox
    def mb_cmd(self, *args):
        return [self.mb, *args]

    def mb_run_args(self, extra, command, detached=False):
        argv = [self.mb, "run"] + (["-d"] if detached else [])
        return argv + ["--rootfs", self.rootfs] + extra + ["--"] + command

    def start_detached(self, extra, command):
        r = subprocess.run(self.mb_run_args(extra, command, True), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=self.env, timeout=CALL_TIMEOUT)
        cid = r.stdout.strip()
        if r.returncode != 0 or not re.fullmatch(r"[0-9a-f]{12}", cid):
            raise RuntimeError("minibox run -d failed: rc=%d %s" % (r.returncode, r.stderr.strip()))
        return cid

    def state(self, cid):
        out = {}
        try:
            with open(os.path.join(self.state_dir, cid, "state")) as f:
                for line in f:
                    k, _, v = line.rstrip("\n").partition("=")
                    out[k] = v
        except OSError:
            pass
        return out

    def wait_running(self, cid, secs=15):
        deadline = time.time() + secs
        while time.time() < deadline:
            st = self.state(cid)
            if st.get("status") == "running" and int(st.get("pid", "0") or 0) > 0:
                return st
            time.sleep(0.05)
        raise RuntimeError("container %s did not reach running state" % cid)

    def container_ids(self):
        try:
            return [d for d in os.listdir(self.state_dir)
                    if re.fullmatch(r"[0-9a-f]{12}", d)]
        except OSError:
            return []

    def kill_container(self, cid):
        """Fast kill: cgroup.kill (kills every process of the box at once), then SIGKILL of the
        init from the state file for boxes without a cgroup, then `minibox rm -f`. A PID-1
        `sleep` ignores SIGTERM, so the normal stop would wait out its whole grace period."""
        try:
            with open("%s/box-%s/cgroup.kill" % (CG_BASE, cid), "w") as f:
                f.write("1")
        except OSError:
            pass
        try:
            pid = int(self.state(cid).get("pid", "0") or 0)
            if pid > 1:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass
        try:
            subprocess.run(self.mb_cmd("rm", "-f", cid), stdin=subprocess.DEVNULL,
                           capture_output=True, env=self.env, timeout=CALL_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            self.errors.append("rm -f %s: %s" % (cid, e))

    def cleanup(self):
        """Remove every container, host sleep and docker container we started. Never raises.
        Returns a short description of what was cleaned."""
        done = []
        for p in self.host_procs:
            try:
                p.kill()
                p.wait(timeout=5)
                done.append("host sleep %d" % p.pid)
            except Exception:
                pass
        self.host_procs = []
        for cid in self.container_ids():
            self.kill_container(cid)
            done.append("container " + cid)
        for name in self.docker_names:
            try:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
                done.append("docker " + name)
            except Exception:
                pass
        self.docker_names = []
        return done

    def fail(self, kind, what):
        """Record an ERROR/TIMEOUT: printed, remembered for the exit code, never raised."""
        self.errors.append("%s: %s" % (kind, what))
        self.say("%s: %s" % (kind, what))

    def flush_limits(self):
        self.write_csv("limits.csv", ["test", "expected", "measured", "tolerance", "status", "detail"],
                       self.limit_rows)

    def leftovers(self):
        """List of things that should not exist after cleanup."""
        left = ["cgroup " + p for p in glob.glob(CG_BASE + "/box-*")]
        ip = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True)
        left += ["interface " + m for m in re.findall(r"\d+: (vethh[^:@\s]*)", ip.stdout)]
        left += ["state " + c for c in self.container_ids()]
        return left

    # ------------------------------------------------------------- docker
    def detect_docker(self):
        if not shutil.which("docker"):
            self.docker_note = "docker CLI not found"
            return
        try:
            r = subprocess.run(["docker", "image", "inspect", DOCKER_IMAGE],
                               capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.docker_note = "docker not usable: %s" % e
            return
        if r.returncode != 0:
            self.docker_note = "image '%s' not present locally (never pulled)" % DOCKER_IMAGE
            return
        self.docker_ok = True


# ------------------------------------------------------------ memory helpers
def read_text(path):
    with open(path) as f:
        return f.read()


def proc_mem_bytes(pid):
    """(bytes, kind): PSS from smaps_rollup, else RSS from status. (None, 'none') if gone."""
    try:
        for line in read_text("/proc/%d/smaps_rollup" % pid).splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) * 1024, "pss"
    except OSError:
        pass
    try:
        for line in read_text("/proc/%d/status" % pid).splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024, "rss"
    except OSError:
        pass
    return None, "none"


def parent_pid(pid):
    # field 2 after the "(comm)" part of /proc/<pid>/stat, robust against spaces in comm
    return int(read_text("/proc/%d/stat" % pid).rsplit(")", 1)[1].split()[1])


def cgroup_memory_current(path):
    try:
        return int(read_text(os.path.join(path, "memory.current")))
    except (OSError, ValueError):
        return None


def pid_cgroup_dir(pid):
    for line in read_text("/proc/%d/cgroup" % pid).splitlines():
        if line.startswith("0::"):
            return "/sys/fs/cgroup" + line[3:]
    return None


# ------------------------------------------------------------------ startup
def timed(argv, env, timeout=CALL_TIMEOUT):
    """(elapsed_ns, returncode); returncode is TIMEOUT_RC if the call hit its timeout."""
    t0 = time.perf_counter_ns()
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=env, timeout=timeout)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = TIMEOUT_RC
    return time.perf_counter_ns() - t0, rc


def startup_env(base_env, name):
    """Environment for one startup configuration: base_env plus its extra variables, if any."""
    return dict(base_env, **STARTUP_EXTRA_ENV.get(name, {}))


def startup_configs(b):
    """Ordered dict name -> (argv or None, note). None means 'not measured'."""
    cfgs = {}
    true_bin = shutil.which("true") or "/bin/true"
    cfgs["host_true"] = ([true_bin], "fork+exec of true on the host")
    if shutil.which("unshare"):
        cfgs["unshare_ns"] = (["unshare", "--fork", "--pid", "--mount-proc", "--uts", "--ipc",
                               "--net", true_bin], "host unshare, namespaces only")
    else:
        cfgs["unshare_ns"] = (None, "unshare not found")
    cfgs["minibox_nolimits"] = (b.mb_run_args(["--net", "none"], ["/bin/true"]),
                                "minibox run --net none, no limits")
    cfgs["minibox_limits"] = (b.mb_run_args(["--net", "none"] + LIMITS_FOR_STARTUP, ["/bin/true"]),
                              "minibox run --net none + memory/cpu/pids limits (clone3 into the cgroup)")
    cfgs["minibox_limits_migrate"] = (
        b.mb_run_args(["--net", "none"] + LIMITS_FOR_STARTUP, ["/bin/true"]),
        "same limits, MINIBOX_NO_CLONE3=1: clone() then move into the cgroup (old path)")
    cfgs["minibox_veth_limits"] = (b.mb_run_args(["--net", "veth"] + LIMITS_FOR_STARTUP, ["/bin/true"]),
                                   "minibox run --net veth + limits")
    if b.docker_ok:
        cfgs["docker"] = (["docker", "run", "--rm", "--network", "none", DOCKER_IMAGE, "/bin/true"],
                          "docker run --rm --network none alpine /bin/true")
    else:
        cfgs["docker"] = (None, "not measured: " + b.docker_note)
    return cfgs


def bench_startup(b):
    b.say("== A. Startup time (N=%d per configuration, %d warm-up(s), seed %d) ==" %
          (b.runs, b.warmups, b.seed))
    cfgs = startup_configs(b)
    active = {k: v[0] for k, v in cfgs.items() if v[0]}
    for name in active:
        for _ in range(b.warmups):          # discarded: warms page cache, dentries, CPU caches
            if timed(active[name], startup_env(b.env, name))[1] == TIMEOUT_RC:
                b.fail("TIMEOUT", "startup warm-up %s" % name)
                b.cleanup()
    schedule = [name for name in active for _ in range(b.runs)]
    random.Random(b.seed).shuffle(schedule)  # interleave so drift hits every config equally
    raw = []
    for order, name in enumerate(schedule):
        ns, rc = timed(active[name], startup_env(b.env, name))
        raw.append((name, order, ns, rc))
        if rc == TIMEOUT_RC:
            b.fail("TIMEOUT", "startup run %s #%d" % (name, order))
            b.cleanup()             # the timed-out container may still be alive
    b.write_csv("startup_raw.csv", ["config", "order", "ns", "returncode"], raw)

    rows = []
    b.say("%-22s %4s %4s %9s %9s %9s %9s %9s %9s" %
          ("config", "n", "fail", "min_ms", "median", "mean", "p95", "max_ms", "stdev"))
    for name, (argv, note) in cfgs.items():
        if argv is None:
            rows.append([name, "not measured", 0, 0] + [""] * 6 + [note])
            b.say("%-22s not measured (%s)" % (name, note))
            continue
        good = [ns / 1e6 for (n, _, ns, rc) in raw if n == name and rc == 0]
        failed = sum(1 for (n, _, _, rc) in raw if n == name and rc != 0)
        s = stats.summarise(good)
        vals = [s[k] for k in ("min", "median", "mean", "p95", "max", "stdev")]
        rows.append([name, "measured", s["n"], failed] + ["%.3f" % v if v is not None else "" for v in vals] + [note])
        b.say("%-22s %4d %4d " % (name, s["n"], failed) +
              " ".join("%9s" % ("%.2f" % v if v is not None else "-") for v in vals))
    b.write_csv("startup_summary.csv",
                ["config", "status", "n", "failed", "min_ms", "median_ms", "mean_ms", "p95_ms",
                 "max_ms", "stdev_ms", "note"], rows)
    b.say()


# ------------------------------------------------------------------- memory
def sample_group(pids_init, pids_other, cg_paths):
    """Return (cgroup_total, init_total, other_total, kinds) summed over the containers."""
    cg = [cgroup_memory_current(p) for p in cg_paths if p]
    init = [proc_mem_bytes(p) for p in pids_init]
    other = [proc_mem_bytes(p) for p in pids_other]
    kinds = sorted({k for _, k in init + other})
    total = lambda xs: sum(x for x in xs if x is not None) if any(x is not None for x in xs) else None  # noqa: E731
    return (total(cg), total([x for x, _ in init]), total([x for x, _ in other]), "+".join(kinds))


def measure_minibox_memory(b, k):
    ids = [b.start_detached(["--net", "none", "--memory", "1G"], ["sleep", "300"]) for _ in range(k)]
    sts = [b.wait_running(c) for c in ids]
    time.sleep(0.5)
    cg, init, mon, kinds = sample_group([int(s["pid"]) for s in sts], [int(s["monitor_pid"]) for s in sts],
                                        ["%s/box-%s" % (CG_BASE, c) for c in ids])
    total = (cg or 0) + (mon or 0)      # the monitor is outside the container's cgroup
    return [cg, init, mon, total, kinds]


def measure_host_memory(b, k):
    procs = [subprocess.Popen(["sleep", "300"], stdin=subprocess.DEVNULL) for _ in range(k)]
    b.host_procs += procs
    time.sleep(0.3)
    _, init, _, kinds = sample_group([p.pid for p in procs], [], [])
    return [None, init, None, init, kinds]


def measure_docker_memory(b, k):
    names = []
    for i in range(k):
        name = "mbbench-%d-%d-%d" % (os.getpid(), k, i)
        b.docker_names.append(name)
        r = subprocess.run(["docker", "run", "-d", "--network", "none", "--name", name,
                            DOCKER_IMAGE, "sleep", "300"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("docker run failed: " + r.stderr.strip())
        names.append(name)
    pids = []
    for name in names:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", name],
                           capture_output=True, text=True, timeout=30)
        pids.append(int(r.stdout.strip()))
    time.sleep(0.5)
    shims = [parent_pid(p) for p in pids]
    cg, init, shim, kinds = sample_group(pids, shims, [pid_cgroup_dir(p) for p in pids])
    return [cg, init, shim, (cg or 0) + (shim or 0), kinds]


def bench_memory(b):
    b.say("== B. Memory overhead: container running `sleep 300` (bytes, summed over k containers) ==")
    methods = [("minibox", measure_minibox_memory), ("host_sleep", measure_host_memory)]
    if b.docker_ok:
        methods.append(("docker", measure_docker_memory))
    rows, series = [], {}
    for name, fn in methods:
        for k in MEM_COUNTS:
            try:
                cg, init, other, total, kinds = fn(b, k)
                note = kinds
            except Exception as e:      # keep going; the failure is recorded in the CSV
                cg = init = other = total = None
                kind = "TIMEOUT" if isinstance(e, subprocess.TimeoutExpired) else "ERROR"
                note = "%s: %s" % (kind.lower(), e)
                b.fail(kind, "memory %s k=%d: %s" % (name, k, e))
            finally:
                b.cleanup()
            per = total / k if total is not None else None
            rows.append([name, k, cg, init, other, total, "%.0f" % per if per is not None else "", note])
            if total is not None:
                series.setdefault(name, []).append((k, total))
            b.say("%-11s k=%-2d cgroup=%-10s init=%-10s monitor/shim=%-10s total=%-10s per-container=%s %s" %
                  (name, k, cg, init, other, total, "%.0f" % per if per is not None else "-", note))
    if not b.docker_ok:
        rows.append(["docker", "", "", "", "", "", "", "not measured: " + b.docker_note])
        b.say("docker      not measured (%s)" % b.docker_note)
    b.write_csv("memory.csv", ["config", "k", "cgroup_memory_current_total", "init_mem_total",
                               "monitor_or_shim_mem_total", "total_bytes", "per_container_bytes", "note"], rows)
    inc = []
    for name, pts in series.items():
        slope = stats.incremental_cost(pts)
        inc.append([name, "%.0f" % slope if slope is not None else ""])
        b.say("incremental cost per extra container (%s): %s bytes" %
              (name, "%.0f" % slope if slope is not None else "n/a"))
    b.write_csv("memory_incremental.csv", ["config", "incremental_bytes_per_container"], inc)
    b.say()


# ------------------------------------------------------------------- limits
STAT_RE = {
    "memory_peak": re.compile(r"\[stats\] memory\.peak\s+(\d+)"),
    "cpu_usec": re.compile(r"\[stats\] cpu\.usage_usec\s+(\d+)"),
    "pids_peak": re.compile(r"\[stats\] pids\.peak\s+(\d+)"),
    "oom_kill": re.compile(r"\[stats\] oom_kill\s+(\d+)"),
}


def run_with_stats(b, extra, shell_cmd, timeout=CALL_TIMEOUT):
    """Foreground `minibox run --stats`; minibox prints the cgroup files before removing the
    cgroup, so we parse those lines. Returns dict with rc, wall_us, stdout, stderr and stats.
    On timeout the container is killed at once and subprocess.TimeoutExpired is re-raised."""
    argv = b.mb_run_args(["--net", "none"] + extra + ["--stats"], ["/bin/sh", "-c", shell_cmd])
    t0 = time.perf_counter_ns()
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           env=b.env, timeout=timeout)
    except subprocess.TimeoutExpired:
        b.cleanup()                 # run was killed by subprocess; its container was not
        raise
    out = {"rc": r.returncode, "wall_us": (time.perf_counter_ns() - t0) / 1000.0,
           "stdout": r.stdout, "stderr": r.stderr}
    for key, rx in STAT_RE.items():
        m = rx.search(r.stderr)
        out[key] = int(m.group(1)) if m else None
    return out


def mib(x):
    return "n/a" if x is None else "%.1f MiB" % (x / 1048576.0)


def add_check(b, name, expected, measured, tolerance, ok, detail=""):
    status = "OK" if ok else "FAIL"
    b.limit_rows.append([name, expected, measured, tolerance, status, detail])
    b.flush_limits()
    b.say("%s: %s | expected %s | measured %s%s" %
          (status, name, expected, measured, " | " + detail if detail else ""))


def limit_group(b, names, fn):
    """Run one group of checks. Any error or timeout becomes ERROR/TIMEOUT rows for the group's
    checks (so the CSV is complete) and the run continues."""
    try:
        fn()
    except subprocess.TimeoutExpired:
        status, detail = "TIMEOUT", "no result within %d s; container killed" % CALL_TIMEOUT
    except Exception as e:      # keep going: one broken step must not lose the other results
        status, detail = "ERROR", "%s: %s" % (type(e).__name__, e)
    else:
        return
    done = {r[0] for r in b.limit_rows}
    for n in names:
        if n not in done:
            b.limit_rows.append([n, "-", "-", "-", status, detail])
            b.say("%s: %s | %s" % (status, n, detail))
    b.errors.append("%s: limit group %s" % (status, names[0].split("/")[0]))
    b.flush_limits()
    b.cleanup()


def bench_limits(b):
    b.say("== C. Limit enforcement (expected vs measured) ==")
    limit = 64 * 1048576

    def memory_checks():
        r = run_with_stats(b, ["--memory", "64M"], memory_hog_workload(), 30)
        add_check(b, "memory/exit_code", "137 (OOM-killed)", r["rc"], "exact", r["rc"] == 137)
        add_check(b, "memory/oom_kill", ">= 1", r["oom_kill"], "memory.events", (r["oom_kill"] or 0) >= 1)
        add_check(b, "memory/peak_vs_limit", "<= 1.10 x 64 MiB", mib(r["memory_peak"]), "10 %",
                  r["memory_peak"] is not None and r["memory_peak"] <= limit * 1.10, "memory.peak")

    def memory_control():
        c = run_with_stats(b, ["--memory", "1G"], memory_control_workload(), 30)
        add_check(b, "memory/control_exceeds_64M", "exit 0 and peak > 64 MiB (limit raised to 1G)",
                  "exit %s, peak %s" % (c["rc"], mib(c["memory_peak"])), "-",
                  c["rc"] == 0 and c["memory_peak"] is not None and c["memory_peak"] > limit,
                  "bounded workload shows it needs more than 64M")

    def pids_checks():
        p = run_with_stats(b, ["--pids", "20"], pids_workload(60))
        fork_failed = "started_all" not in p["stdout"] or "fork" in p["stderr"].lower()
        add_check(b, "pids/peak", "<= 20", p["pids_peak"], "exact",
                  p["pids_peak"] is not None and p["pids_peak"] <= 20, "pids.peak")
        add_check(b, "pids/fork_failure", "fork refused for some of 60 children",
                  "yes" if fork_failed else "no", "-", fork_failed)

    def cpu_limited():
        lim = run_with_stats(b, ["--cpus", "0.5"], cpu_workload(b.cpu_secs), b.cpu_secs + 15)
        ratio = lim["cpu_usec"] / lim["wall_us"] if lim["cpu_usec"] is not None else None
        add_check(b, "cpu/ratio_at_0.5", "0.40 - 0.60 CPU", "n/a" if ratio is None else "%.3f" % ratio,
                  "0.40-0.60", ratio is not None and 0.40 <= ratio <= 0.60,
                  "usage_usec / wall time, ~%d s busy loop" % b.cpu_secs)

    def cpu_control():
        big = str(min(2, os.cpu_count() or 1))
        ctl = run_with_stats(b, ["--cpus", big], cpu_workload(b.cpu_secs), b.cpu_secs + 15)
        cratio = ctl["cpu_usec"] / ctl["wall_us"] if ctl["cpu_usec"] is not None else None
        add_check(b, "cpu/control_ratio", "> 0.80 CPU (limit %s CPUs, one busy loop)" % big,
                  "n/a" if cratio is None else "%.3f" % cratio, "> 0.80",
                  cratio is not None and cratio > 0.80, "control: loop is not throttled")

    limit_group(b, ["memory/exit_code", "memory/oom_kill", "memory/peak_vs_limit"], memory_checks)
    limit_group(b, ["memory/control_exceeds_64M"], memory_control)
    limit_group(b, ["pids/peak", "pids/fork_failure"], pids_checks)
    limit_group(b, ["cpu/ratio_at_0.5"], cpu_limited)
    limit_group(b, ["cpu/control_ratio"], cpu_control)
    b.flush_limits()
    b.say()


# ------------------------------------------------------------------- system
def cpu_model():
    try:
        for line in read_text("/proc/cpuinfo").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def mem_total_kb():
    try:
        for line in read_text("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    except OSError:
        pass
    return None


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def system_info(b, sections):
    rel = platform.release()
    info = {
        "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "kernel": rel,
        "kernel_version": platform.version(),
        "cpu_model": cpu_model(),
        "cpu_cores": os.cpu_count(),
        "ram_kb": mem_total_kb(),
        "wsl": "microsoft" in rel.lower() or "WSL_DISTRO_NAME" in os.environ,
        "python": platform.python_version(),
        "sections": sections,
        "quick": b.quick,
        "runs_per_config": b.runs,
        "warmups": b.warmups,
        "cpu_busy_seconds": b.cpu_secs,
        "shuffle_seed": b.seed,
        "docker": None,
        "docker_note": b.docker_note,
    }
    if os.path.exists(b.mb):
        info["minibox_build_time"] = datetime.datetime.fromtimestamp(
            os.path.getmtime(b.mb)).astimezone().isoformat(timespec="seconds")
        info["minibox_sha256"] = sha256_of(b.mb)
        info["minibox_version"] = "build %s (sha256 %s)" % (info["minibox_build_time"],
                                                           info["minibox_sha256"][:12])
    if shutil.which("docker"):
        r = subprocess.run(["docker", "--version"], capture_output=True, text=True)
        info["docker"] = r.stdout.strip() or None
    return info


def chown_tree(path):
    """When run through sudo, give the results back to the invoking user."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if not uid or not gid:
        return
    for root, dirs, files in os.walk(path):
        for n in [root] + [os.path.join(root, x) for x in dirs + files]:
            try:
                os.chown(n, int(uid), int(gid))
            except OSError:
                pass


def publish_latest(b):
    latest = os.path.join(b.base_out, "latest")
    if os.path.islink(latest) or os.path.isfile(latest):
        os.unlink(latest)
    elif os.path.isdir(latest):
        shutil.rmtree(latest)
    shutil.copytree(b.run_dir, latest)
    chown_tree(latest)


# --------------------------------------------------------------------- main
def parse_args(argv):
    ap = argparse.ArgumentParser(description="minibox benchmarks (need root)")
    ap.add_argument("what", choices=["startup", "memory", "limits", "all"])
    ap.add_argument("--quick", action="store_true", help="N=5, 1 warm-up, 2 s CPU test")
    ap.add_argument("--runs", type=int, help="runs per startup configuration (default 30, quick 5)")
    ap.add_argument("--seed", type=int, help="seed for the random run order (default: time)")
    ap.add_argument("--out", default=os.path.join(ROOT_DIR, "bench", "results"),
                    help="base directory for results (default bench/results)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if os.geteuid() != 0:
        print("bench.py must run as root (minibox needs it): sudo python3 bench/bench.py %s" % args.what)
        return 1
    if args.runs is not None and args.runs < 1:
        print("--runs must be at least 1")
        return 1
    if not os.access(os.path.join(ROOT_DIR, "minibox"), os.X_OK):
        print("minibox not built; run 'make' first.")
        return 1
    if not os.path.exists(os.path.join(ROOT_DIR, "rootfs", "bin", "busybox")):
        print("Alpine rootfs missing; run 'make rootfs' first.")
        return 1

    b = Bench(args)

    def on_signal(signum, _frame):      # SIGINT/SIGTERM: unwind to the finally block, which cleans up
        raise KeyboardInterrupt(signal.Signals(signum).name)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    sections = ["startup", "memory", "limits"] if args.what == "all" else [args.what]
    runners = {"startup": ("A", bench_startup), "memory": ("B", bench_memory), "limits": ("C", bench_limits)}
    status = 0
    interrupted = False
    t_start = time.monotonic()
    try:
        pre = b.leftovers()
        if pre:
            print("Leftover box-* cgroups / vethh* interfaces exist; remove them first:\n  " + "\n  ".join(pre))
            return 1
        os.makedirs(b.run_dir)
        b.detect_docker()
        b.say("minibox benchmarks %s%s" % (b.stamp, " (quick)" if b.quick else ""))
        if not b.docker_ok:
            b.say("NOTE: docker comparison not measured (%s)" % b.docker_note)
        b.say()
        for sec in sections:
            letter, fn = runners[sec]
            t0 = time.monotonic()
            try:
                fn(b)
            except Exception as e:      # a whole section failing must not lose the others
                b.fail("ERROR", "section %s (%s): %s: %s" % (letter, sec, type(e).__name__, e))
                b.cleanup()
            b.say("Section %s (%s) took %.1f s" % (letter, sec, time.monotonic() - t0))
            b.say()
    except KeyboardInterrupt as e:
        interrupted = True
        print("\ninterrupted (%s); cleaning up" % e)
    finally:
        cleaned = b.cleanup()
        if interrupted:
            b.say("INTERRUPTED: cleaned %d item(s): %s" % (len(cleaned), ", ".join(cleaned) or "none"))
        left = b.leftovers()
        if left:
            b.say("CLEANUP: FAIL, left behind: " + ", ".join(left))
        else:
            b.say("CLEANUP: OK (no box-* cgroup, vethh* interface or container state left)")
        b.say("Total time %.1f s" % (time.monotonic() - t_start))
        if b.errors:
            b.say("%d ERROR/TIMEOUT event(s): %s" % (len(b.errors), "; ".join(b.errors)))
        if interrupted:
            status = 130
        elif left or b.errors:
            status = 1
        if os.path.isdir(b.run_dir):
            with open(os.path.join(b.run_dir, "system.json"), "w") as f:
                json.dump(system_info(b, sections), f, indent=2)
            with open(os.path.join(b.run_dir, "summary.txt"), "w") as f:
                f.write("\n".join(b.lines) + "\n")
            chown_tree(b.run_dir)
            if not interrupted:         # results are published even after ERROR/TIMEOUT rows
                publish_latest(b)
                print("Results: %s (copied to %s/latest)" % (b.run_dir, b.base_out))
        shutil.rmtree(b.state_dir, ignore_errors=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
