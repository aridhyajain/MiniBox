# Benchmarks (Phase 6)

Goal: measurable evidence that minibox works and roughly how it compares. All numbers in this project's report must come from files produced by a real run of `bench/bench.py` on the author's machine. Nothing here is estimated or typed in by hand.

## How to run
Benchmarks need root, so `make` only prints the commands:
```sh
make                                          # build ./minibox (and 'make rootfs' once)
sudo bash tests/phase6_test.sh                # checks the tooling, about 1 minute
sudo python3 bench/bench.py all --quick       # N=5, about 1 minute
sudo python3 bench/bench.py all               # N=30, several minutes
python3 bench/make_charts.py bench/results/latest   # no root: writes startup.svg, memory.svg, limits.svg there
```
Parts can be run alone: `startup`, `memory`, `limits`. Options: `--runs N`, `--seed S`, `--out DIR`. Close other heavy programs first; do not run on battery.
Results: `bench/results/<timestamp>/` (copied to `bench/results/latest/`): `startup_raw.csv`, `startup_summary.csv`, `memory.csv`, `memory_incremental.csv`, `limits.csv`, `system.json`, `summary.txt`.
Code: `docs/bench/bench.md`, `docs/bench/stats.md`, `docs/bench/make_charts.md`.

**Keeping one reference run in the repository.** `.gitignore` ignores `bench/results/*` except `bench/results/reference*/` (so an older run can be kept next to it, e.g. `bench/results/reference-before-clone3/`). Copy the run you choose there
(`cp -r bench/results/<timestamp> bench/results/reference`, and generate its charts with `make_charts.py bench/results/reference`); everything else stays local.

## A. Startup time
Wall-clock time from launching the command until it has exited, running `/bin/true`, measured with `time.perf_counter_ns()` (monotonic clock) around `subprocess.run`.

| # | Configuration | What it includes |
|---|---------------|------------------|
| 1 | `host_true` | fork+exec on the host: the floor |
| 2 | `unshare_ns` | host `unshare --fork --pid --mount-proc --uts --ipc --net`: namespaces only (skipped if `unshare` is missing) |
| 3 | `minibox_nolimits` | minibox, `--net none`: namespaces + `pivot_root` + mounts + state file + monitor |
| 4 | `minibox_limits` | as 3 plus a cgroup with memory, cpu, pids limits |
| 5 | `minibox_veth_limits` | as 4 plus a veth pair, addresses and routes |
| 6 | `docker` | `docker run --rm --network none alpine /bin/true`; **only** if the docker CLI exists and the `alpine` image is already local; never pulls; else "not measured" |

Method: a few warm-up runs per configuration are discarded (1 in `--quick`, 3 otherwise); then N runs per configuration (30, or 5 in `--quick`) are put in one list and **shuffled** (seed saved in `system.json`) so drift hits all configurations equally. A run with a non-zero exit code is counted in `failed` and left out of the statistics.
Reported per configuration (milliseconds): n, min, median, mean, p95, max, standard deviation. Median is the typical run; p95 is the slow tail; the mean is pulled by outliers.

## B. Memory overhead
For `sleep 300`, with 1, 5 and 10 containers running at once:
- `cgroup_memory_current_total`: sum of `memory.current` of the containers' cgroups (includes page cache charged to the container).
- `init_mem_total`: sum of the PSS of each container's init process (PSS = proportional set size: shared pages are divided among the processes that share them; falls back to RSS if `smaps_rollup` is missing; the `note` column says which).
- `monitor_or_shim_mem_total`: PSS of the minibox monitor process on the host (for docker: its `containerd-shim`). It sits outside the container cgroup.
- `total_bytes` = cgroup total + monitor total (init PSS is not added: it is shown for information and is not simply a subset of the cgroup number, see Analysis). `per_container_bytes` = total / k.
- `memory_incremental.csv`: the least-squares slope of total versus k, the memory cost of one extra container.
- Baseline: the PSS of a plain host `sleep 300`; docker if available.
A `--memory 1G` limit is given to minibox containers so that a cgroup exists to read `memory.current` from (minibox creates no cgroup without a limit). 1G is never reached; the limit itself costs no memory.

## C. Limit enforcement
Each check has an expected value, a measured value and a status (`limits.csv`, and printed): `OK`, `FAIL` (kernel behaved differently from expected), `ERROR` (the step crashed) or `TIMEOUT` (no answer in time). A broken step gives ERROR/TIMEOUT rows instead of aborting the run, and bench.py then exits non-zero. The workloads use only busybox tools already in the Alpine rootfs; the cgroup counters come from `minibox run --stats`, which prints them just before the cgroup is deleted.

| Check | Workload | Expected | Evidence |
|-------|----------|----------|----------|
| `memory/exit_code` | `--memory 64M`, awk doubles a string forever | exit 137 | OOM kill |
| `memory/oom_kill` | same | `oom_kill` >= 1 | `memory.events` |
| `memory/peak_vs_limit` | same | `memory.peak` <= 1.10 x 64 MiB | `memory.peak` |
| `memory/control_exceeds_64M` | `--memory 1G`, bounded awk | exit 0 and `memory.peak` > 64 MiB | shows the workload really needs more than 64M |
| `pids/peak` | `--pids 20`, 60 background `sleep 5` | `pids.peak` <= 20 | `pids.peak` |
| `pids/fork_failure` | same | some forks refused | shell did not finish the loop / fork error text |
| `cpu/ratio_at_0.5` | `--cpus 0.5`, busy loop for 5 s (2 s in quick) | 0.40 to 0.60 CPU | `cpu.stat usage_usec` / wall time |
| `cpu/control_ratio` | `--cpus 2` (or core count if less), same loop | > 0.80 CPU | the loop is not throttled |

The controls use a high limit rather than no limit, because minibox only creates a cgroup (and so only has counters to read) when a limit is set. The CPU ratio divides by the wall time of the whole `minibox run`, which includes setup and teardown, so it reads a little low, more so with the short quick run.

## How to read the charts
- `startup.svg`: one bar per configuration, bar height = median, whisker = p95; lower is better. **The y axis is a log scale** (each gridline is about 2 to 2.5 times the previous one), so that 1.7 ms and 165 ms fit on one chart; compare ratios, not bar heights. The gap between bars 1 and 3 is the cost of becoming a container. A missing configuration has a "not measured" note.
- `memory.svg`: for 1, 5, 10 containers, memory per container (MiB) as three bars (cgroup `memory.current`, init PSS, monitor PSS); the dashed line is a plain host `sleep`. If the bars stay flat as k grows, the cost per container is constant; per-container cost near the 10-container value is the marginal cost.
- `limits.svg`: the table of the eight checks with expected, measured and status OK/FAIL/ERROR/TIMEOUT (a mark and the word, so it works without colour).
Charts adapt to light and dark browsers and are plain SVG files (open them in a browser).

## Limitations (read before quoting a number)
- **WSL2 is a virtual machine**, not bare metal: timings include the hypervisor, and results move with the Windows host load, power plan and background activity.
- Timings include the **Python `subprocess` overhead** (fork/exec of the child, pipes), the same for every configuration but not zero. Differences between configurations are meaningful; absolute values are slightly high.
- **Page cache and warm-up**: after warm-ups the rootfs and binaries are cached. A cold start (first run after boot) is slower and is not measured.
- The **Docker comparison is a rough reference only**: Docker has a daemon and client round trip, an overlay filesystem, seccomp and capability setup, and a different network model. minibox does less, so it is not a fair like-for-like.
- **N is small** (30, or 5 in quick mode): p95 from 30 runs rests on one or two values. Use the same N when comparing.
- **Results vary between runs**; one reference run is a sample, not a constant. `system.json` records the machine, kernel, seed and options.
- The memory numbers depend on what is counted (PSS vs RSS vs `memory.current`); see section B. Page cache in `memory.current` is reclaimable.
- **Workloads need a shell as PID 1.** The container's init ignores signals from inside its own PID namespace unless it has a handler, so a workload whose last command is exec'd as PID 1 (for example `timeout`) can hang the run. Every workload therefore ends with `; exit 0`, and each external call has a timeout. See `docs/VIVA_NOTES.md`, Phase 6. (With `minibox run --init` this particular hang cannot happen, because `timeout` is then the init's child, not PID 1; the benchmarks still run without `--init`, which is the default and what was measured.)
- The CPU test needs a CPU not already saturated by other work; a busy host lowers the measured ratio.

## Results

### Machine and run

- Run date: 2026-10-19T11:27:24+00:00
- Kernel: 6.18.33.2-microsoft-standard-WSL2
- CPU: 12th Gen Intel(R) Core(TM) i3-1215U, 8 cores
- RAM: 12088948 kB (11.53 GiB)
- WSL: yes
- Runs per configuration: 30, warm-ups: 3, CPU busy loop: 5 s, shuffle seed: 1792409225
- minibox build: build 2026-10-19T09:56:32+00:00 (sha256 78fff6a9bdcf)
- Docker: not measured (docker CLI not found)

### Startup time (ms, wall clock for `/bin/true`)

| Configuration | n | min (ms) | median (ms) | mean (ms) | p95 (ms) | stdev (ms) |
|---|---|---|---|---|---|---|
| host_true | 30 | 1.454 | 1.716 | 1.982 | 3.640 | 0.703 |
| unshare_ns | 30 | 3.527 | 3.893 | 4.364 | 8.377 | 1.567 |
| minibox_nolimits | 30 | 3.439 | 3.994 | 5.733 | 9.242 | 2.448 |
| minibox_limits | 30 | 3.506 | 32.124 | 21.922 | 33.828 | 16.043 |
| minibox_veth_limits | 30 | 114.504 | 165.379 | 150.778 | 194.342 | 30.341 |
| docker | not measured: docker CLI not found | - | - | - | - | - |

### Memory per container (`sleep 300`)

| Configuration | k | cgroup memory.current / k (MiB) | init PSS / k (MiB) | monitor PSS / k (MiB) | total / k (MiB) | total / k (bytes) |
|---|---|---|---|---|---|---|
| minibox | 1 | 0.24 | 0.96 | 0.21 | 0.46 | 477184 |
| minibox | 5 | 0.24 | 0.25 | 0.19 | 0.42 | 444826 |
| minibox | 10 | 0.26 | 0.15 | 0.19 | 0.45 | 476774 |
| host_sleep | 1 | - | 5.02 | - | 5.02 | 5261312 |
| host_sleep | 5 | - | 2.12 | - | 2.12 | 2222080 |
| host_sleep | 10 | - | 1.75 | - | 1.75 | 1835315 |
| docker | - | - | - | - | - | not measured: docker CLI not found |

### Incremental memory cost per extra container

| Configuration | extra cost per container (bytes) | (MiB) |
|---|---|---|
| minibox | 478040 | 0.46 |
| host_sleep | 1454399 | 1.39 |

### Limit enforcement

| Check | Expected | Measured | Status |
|---|---|---|---|
| memory/exit_code | 137 (OOM-killed) | 137 | OK |
| memory/oom_kill | >= 1 | 3 | OK |
| memory/peak_vs_limit | <= 1.10 x 64 MiB | 64.0 MiB | OK |
| memory/control_exceeds_64M | exit 0 and peak > 64 MiB (limit raised to 1G) | exit 0, peak 192.9 MiB | OK |
| pids/peak | <= 20 | 20 | OK |
| pids/fork_failure | fork refused for some of 60 children | yes | OK |
| cpu/ratio_at_0.5 | 0.40 - 0.60 CPU | 0.508 | OK |
| cpu/control_ratio | > 0.80 CPU (limit 2 CPUs, one busy loop) | 0.997 | OK |

Source: every number in this section comes from the files in `bench/results/reference/` (at the time of writing the run made BEFORE the clone3 change; it has no `minibox_limits_migrate` row) (`startup_summary.csv`, `startup_raw.csv`, `memory.csv`, `memory_incremental.csv`, `limits.csv`, `system.json`). The tables above are printed by `python3 bench/make_report_tables.py bench/results/reference`. Docker was **not measured** (the docker CLI is not installed), so there is no Docker number anywhere in this report.

### Charts
The charts below are the SVG files from the same run.

**Startup chart** (`startup.svg`): one bar per configuration, bar = median, whisker up to p95. **The y axis is a log scale**, so equal distances mean equal ratios, not equal differences; a bar twice as tall is not twice as slow.

![Startup time, log scale](../bench/results/reference/startup.svg)

**Memory chart** (`memory.svg`): memory per container for 1, 5 and 10 containers (cgroup `memory.current`, init PSS, monitor PSS); the dashed line is a plain host `sleep`. Linear axis.

![Memory per container](../bench/results/reference/memory.svg)

**Limits chart** (`limits.svg`): the table of expected versus measured with the status of each check.

![Limit enforcement](../bench/results/reference/limits.svg)

## Analysis
This section explains the tables in plain language. Differences are computed from the files; anything that is a guess is labelled as one.

### Startup
- **Plain process, `unshare` and minibox without limits are close.** Medians: `host_true` 1.716 ms, `unshare_ns` 3.893 ms, `minibox_nolimits` 3.994 ms. Creating the namespaces costs 3.893 - 1.716 = 2.177 ms over a plain process. minibox without limits is only 3.994 - 3.893 = 0.101 ms above raw `unshare`. So `pivot_root`, the mounts, the state file and the monitor process add very little on top of namespace creation. The minimums agree (3.527 ms for `unshare_ns`, 3.439 ms for `minibox_nolimits`).
- **A caveat on that comparison.** The mean of `minibox_nolimits` (5.733 ms) is higher than that of `unshare_ns` (4.364 ms), and its standard deviation is larger (2.448 ms against 1.567 ms). In `startup_raw.csv`, 12 of the 30 `minibox_nolimits` runs took 6 ms or more, against 3 of 30 for `unshare_ns`: some runs are in a slower group of about 8 ms. The cause was not investigated. The median hides this, which is why the table also shows the mean and p95 (9.242 ms against 8.377 ms).
- **With limits (`minibox_limits`, measured BEFORE the clone3 change): the cost was a cgroup migration.** In this reference run the median is 32.124 ms, which is 8.04 times the 3.994 ms of `minibox_nolimits` (28.130 ms more), but the minimum is 3.506 ms against 3.439 ms (0.067 ms more); 17 of 30 runs took 30 ms or more and 13 took under 10 ms. The benchmark alone could not say why, so the cause was then investigated with `strace -f -ttt -T` on `minibox run --net none --memory 64M --cpus 0.5 --pids 20 -- /bin/true` (the child was blocked reading the sync pipe at that moment). **Evidence:** the parent's `write` of the child's PID to the container cgroup's `cgroup.procs` took **10.6, 35.7, 8.7, 13.3 and 13.0 ms** in five runs, and no other syscall was slow. Total traced time was 26-57 ms with limits against 14-15 ms without a cgroup. Writing a PID to `cgroup.procs` *migrates* a running task into another cgroup. **The likely mechanism (stated as the likely cause, not proven):** a migration takes a global kernel lock that can wait for an RCU grace period, which is expensive in a virtual machine such as WSL2. What the strace shows for certain is *where* the time goes (that one write); the kernel-side reason was not measured. strace slows every syscall, so its absolute numbers are not the benchmark's, and five traced runs do not explain why 13 of the 30 benchmark runs were fast. **Fix:** create the cgroup and write its limits first, then create the container directly inside it with `clone3(CLONE_INTO_CGROUP)`, so no migration happens (`docs/src/container.md`). The old path stays as `minibox_limits_migrate` (`MINIBOX_NO_CLONE3=1`) so a single benchmark run shows both. 
  <!-- PLACEHOLDER (after-clone3 numbers): to be filled from the new reference run (`bench/results/reference/`; the run above is kept as `bench/results/reference-before-clone3/`). Fill in: `minibox_limits` (clone3) median/min/p95, `minibox_limits_migrate` median/min/p95, `minibox_nolimits` for comparison, and the difference. Until then the numbers in this section are the BEFORE numbers. -->
  **After the fix: _not yet measured_ (placeholder).** Expectation to check, not a result: `minibox_limits` close to `minibox_nolimits`, `minibox_limits_migrate` near the old 32 ms.
- **veth (`minibox_veth_limits`): much slower.** Median 165.379 ms, 133.255 ms more than `minibox_limits` (5.15 times) and 41.4 times the median without limits. Even the fastest run (114.504 ms) is slower than the slowest run of every other minibox configuration. The *likely* reason is that the network setup runs several `ip` commands as separate processes and moves interfaces between network namespaces, and each command costs a fork and exec. **This is the likely reason, not something measured.** The raw data also show runs in three groups about 50 ms apart (11 runs under 140 ms, 17 between 140 and 190 ms, 2 above 190 ms); that pattern is observed and not explained.

### Memory
- **The comparison with a host `sleep` is not apples to apples.** The container's `sleep` is the busybox binary, a small program in the Alpine rootfs. The host's `sleep` is the glibc, dynamically linked GNU coreutils program, which maps more libraries. A host `sleep` using more memory (5.02 MiB at k=1, from 5261312 bytes) says nothing about the container runtime, so the host row is only a reference point for the order of magnitude.
- **The real minibox overhead** is the monitor process (a process on the host, outside the cgroup) plus the cgroup's own memory (`memory.current`) for each container. That sum is the "total / k" column: 0.46 MiB at k=1 (477184 bytes), 0.42 MiB at k=5 (444826 bytes), 0.45 MiB at k=10 (476774 bytes). The extra cost of each additional container (slope over k) is 478040 bytes, 0.46 MiB. These are small, nearly flat numbers.
- **Why the init process's PSS per container drops.** The table shows init PSS / k falling: 0.96 MiB at 1 container, 0.25 at 5, 0.15 at 10. PSS (proportional set size) divides each shared page among the processes that share it. All the containers' init processes map the same busybox binary and the same libc pages, so with one container that process pays for all of them, and with ten containers each pays a tenth. The first container carries more of the shared cost. This is expected, **not a measurement error**. The host `sleep` shows the same effect (5.02, 2.12, 1.75 MiB).
- **Init PSS is not added to the total.** The total uses the cgroup number plus the monitor. The init PSS is shown for information; it is not a subset of the cgroup number (at k=1 it is 0.96 MiB, while `memory.current` is 0.24 MiB per container). A likely reason, not verified, is that PSS includes shared file pages that the kernel charged to a different cgroup when they were first read.

### Limit enforcement
All eight checks have status OK in this run.
- **Memory.** With `--memory 64M` the workload was OOM-killed: exit code 137 (expected 137), `memory.events` `oom_kill` = 3 (expected at least 1), and `memory.peak` = 64.0 MiB (expected at most 1.10 x 64 MiB). The control run with the limit raised to 1G exited 0 with a peak of 192.9 MiB, so the workload really needs more than 64 MiB and it was the limit that stopped it.
- **Processes.** With `--pids 20`, `pids.peak` was 20 (expected at most 20), and some of the 60 background children could not be forked (measured: yes).
- **CPU.** With `--cpus 0.5` the busy loop got 0.508 CPUs (expected 0.40 to 0.60). The control with `--cpus 2` got 0.997 CPUs (expected above 0.80, one busy loop cannot use more than one CPU), so the loop itself is not slow.
- **What the counters prove.** `memory.events` is the kernel's own record that it killed a process for reaching the limit (`oom_kill`); the exit code 137 alone could also come from another SIGKILL. `memory.peak` is the highest memory use ever seen in the cgroup, so a peak of 64.0 MiB shows the limit held. `pids.peak` is the highest number of processes ever in the cgroup, so 20 shows `fork()` was refused beyond it. `cpu.stat` `usage_usec` is the CPU time actually consumed; divided by the wall time it gives the CPU share, which shows the throttle.

### Limitations
- **WSL2 is a virtual machine**, so timings include the hypervisor and move with what Windows is doing.
- **N = 30** runs per configuration (3 warm-ups discarded). p95 from 30 runs depends on one or two values.
- **Millisecond timings include Python `subprocess` overhead** (fork, exec, pipes), the same for every configuration but not zero. Differences between configurations are meaningful; absolute values are somewhat high.
- **Docker was not measured**, so there is no comparison with it.
- **Results vary between runs.** This is one run on one machine on one day (see the machine list above); another run would give different numbers, in particular for the `minibox_limits` migration cost and the veth effects.
