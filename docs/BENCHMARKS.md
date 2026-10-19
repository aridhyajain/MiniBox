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

**Keeping one reference run in the repository.** `.gitignore` ignores `bench/results/*` except `bench/results/reference*/` and `bench/results/analysis/` (so an older run can be kept next to it, e.g. `bench/results/reference-before-clone3/`). Copy the run you choose there
(`cp -r bench/results/<timestamp> bench/results/reference`, and generate its charts with `make_charts.py bench/results/reference`); everything else stays local.

## A. Startup time
Wall-clock time from launching the command until it has exited, running `/bin/true`, measured with `time.perf_counter_ns()` (monotonic clock) around `subprocess.run`.

| # | Configuration | What it includes |
|---|---------------|------------------|
| 1 | `host_true` | fork+exec on the host: the floor |
| 2 | `unshare_ns` | host `unshare --fork --pid --mount-proc --uts --ipc --net`: namespaces only (skipped if `unshare` is missing) |
| 3 | `minibox_nolimits` | minibox, `--net none`: namespaces + `pivot_root` + mounts + state file + monitor |
| 4 | `minibox_limits` | as 3 plus a cgroup with memory, cpu, pids limits; the container is created inside the cgroup with `clone3(CLONE_INTO_CGROUP)` |
| 5 | `minibox_limits_migrate` | as 4 but with `MINIBOX_NO_CLONE3=1`: `clone()` then a write to `cgroup.procs` (the old path; added with the clone3 change) |
| 6 | `minibox_veth_limits` | as 4 plus a veth pair, addresses and routes |
| 7 | `docker` | `docker run --rm --network none alpine /bin/true`; **only** if the docker CLI exists and the `alpine` image is already local; never pulls; else "not measured" |

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
- `startup.svg`: one bar per configuration, bar height = median, whisker = p95; lower is better. **The y axis is a log scale** (each gridline is about 2 to 2.5 times the previous one), so that a millisecond-scale bar and a hundred-millisecond bar fit on one chart; compare ratios, not bar heights. The gap between bars 1 and 3 is the cost of becoming a container. A missing configuration has a "not measured" note.
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

The machine is the same in both runs (same kernel, CPU and RAM); date and minibox build differ.

### Machine and run (AFTER the clone3 change: `bench/results/reference/`)

- Run date: 2026-10-19T12:33:44+00:00
- Kernel: 6.18.33.2-microsoft-standard-WSL2
- CPU: 12th Gen Intel(R) Core(TM) i3-1215U, 8 cores
- RAM: 12088948 kB (11.53 GiB)
- WSL: yes
- Runs per configuration: 30, warm-ups: 3, CPU busy loop: 5 s, shuffle seed: 1792413205
- minibox build: build 2026-10-19T12:20:40+00:00 (sha256 b8f5ef8dfdb6)
- Docker: not measured (docker CLI not found)

### Machine and run (BEFORE the clone3 change: `bench/results/reference-before-clone3/`)

- Run date: 2026-10-19T11:27:24+00:00
- Kernel: 6.18.33.2-microsoft-standard-WSL2
- CPU: 12th Gen Intel(R) Core(TM) i3-1215U, 8 cores
- RAM: 12088948 kB (11.53 GiB)
- WSL: yes
- Runs per configuration: 30, warm-ups: 3, CPU busy loop: 5 s, shuffle seed: 1792409225
- minibox build: build 2026-10-19T09:56:32+00:00 (sha256 78fff6a9bdcf)
- Docker: not measured (docker CLI not found)

### Startup time (ms, wall clock for `/bin/true`), AFTER run

| Configuration | n | min (ms) | median (ms) | mean (ms) | p95 (ms) | stdev (ms) |
|---|---|---|---|---|---|---|
| host_true | 30 | 1.391 | 1.913 | 1.913 | 2.347 | 0.279 |
| unshare_ns | 30 | 3.505 | 3.948 | 4.491 | 8.708 | 1.563 |
| minibox_nolimits | 30 | 3.496 | 4.064 | 5.643 | 8.903 | 3.000 |
| minibox_limits | 30 | 3.406 | 3.770 | 4.836 | 8.449 | 1.999 |
| minibox_limits_migrate | 30 | 3.555 | 16.529 | 19.888 | 34.005 | 15.611 |
| minibox_veth_limits | 30 | 114.412 | 115.321 | 122.097 | 165.732 | 17.432 |
| docker | not measured: docker CLI not found | - | - | - | - | - |

### Startup time, BEFORE and AFTER the clone3 change (ms, wall clock for `/bin/true`)

| Configuration | Run | n | median (ms) | mean (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|---|---|
| minibox_nolimits | before | 30 | 3.994 | 5.733 | 9.242 | 9.654 |
| minibox_limits | before | 30 | 32.124 | 21.922 | 33.828 | 64.452 |
| minibox_nolimits | after | 30 | 4.064 | 5.643 | 8.903 | 16.835 |
| minibox_limits | after | 30 | 3.770 | 4.836 | 8.449 | 8.590 |
| minibox_limits_migrate | after | 30 | 16.529 | 19.888 | 34.005 | 65.394 |
| minibox_veth_limits | before | 30 | 165.379 | 150.778 | 194.342 | 217.758 |
| minibox_veth_limits | after | 30 | 115.321 | 122.097 | 165.732 | 166.501 |

BEFORE has no `minibox_limits_migrate` row (the configuration was added with the change). `minibox_limits` BEFORE is the old path (`cgroup.procs`); AFTER it is `clone3`.

### Memory per container (`sleep 300`)

| Configuration | k | cgroup memory.current / k (MiB) | init PSS / k (MiB) | monitor PSS / k (MiB) | total / k (MiB) | total / k (bytes) |
|---|---|---|---|---|---|---|
| minibox | 1 | 0.25 | 0.97 | 0.21 | 0.46 | 485376 |
| minibox | 5 | 0.24 | 0.25 | 0.18 | 0.42 | 437658 |
| minibox | 10 | 0.24 | 0.15 | 0.19 | 0.42 | 444006 |
| host_sleep | 1 | - | 5.23 | - | 5.23 | 5487616 |
| host_sleep | 5 | - | 2.20 | - | 2.20 | 2305843 |
| host_sleep | 10 | - | 1.74 | - | 1.74 | 1819341 |
| docker | - | - | - | - | - | not measured: docker CLI not found |

### Incremental memory cost per extra container

| Configuration | extra cost per container (bytes) | (MiB) |
|---|---|---|
| minibox | 439858 | 0.42 |
| host_sleep | 1408520 | 1.34 |

### Limit enforcement

| Check | Expected | Measured | Status |
|---|---|---|---|
| memory/exit_code | 137 (OOM-killed) | 137 | OK |
| memory/oom_kill | >= 1 | 3 | OK |
| memory/peak_vs_limit | <= 1.10 x 64 MiB | 64.0 MiB | OK |
| memory/control_exceeds_64M | exit 0 and peak > 64 MiB (limit raised to 1G) | exit 0, peak 192.9 MiB | OK |
| pids/peak | <= 20 | 20 | OK |
| pids/fork_failure | fork refused for some of 60 children | yes | OK |
| cpu/ratio_at_0.5 | 0.40 - 0.60 CPU | 0.500 | OK |
| cpu/control_ratio | > 0.80 CPU (limit 2 CPUs, one busy loop) | 0.998 | OK |

### Source of the numbers
Every number in this section comes from the files in `bench/results/reference/` (the run made AFTER the clone3 change) and `bench/results/reference-before-clone3/` (the run made BEFORE it): `startup_summary.csv`, `startup_raw.csv`, `memory.csv`, `memory_incremental.csv`, `limits.csv`, `system.json`, `summary.txt`. The tables are printed by `python3 bench/make_report_tables.py bench/results/reference` and `python3 bench/make_report_tables.py --compare bench/results/reference-before-clone3 bench/results/reference`. Counts of runs ("13 of 30 took ...") are counted in `startup_raw.csv` of the named folder. The probe numbers in the bullet "When the stall happens" come from `bench/results/analysis/attach_stall_probe.txt` (rows quoted by option text and mode); the counts of runs after a veth run are computed from `startup_raw.csv` of the named folder. Docker was **not measured** (the docker CLI is not installed), so there is no Docker number anywhere in this report.

### Charts
The charts below are the SVG files of the AFTER run (`bench/results/reference/`).

**Startup chart** (`startup.svg`): one bar per configuration, bar = median, whisker up to p95. **The y axis is a log scale**, so equal distances mean equal ratios, not equal differences; a bar twice as tall is not twice as slow.

![Startup time, log scale](../bench/results/reference/startup.svg)

**Memory chart** (`memory.svg`): memory per container for 1, 5 and 10 containers (cgroup `memory.current`, init PSS, monitor PSS); the dashed line is a plain host `sleep`. Linear axis.

![Memory per container](../bench/results/reference/memory.svg)

**Limits chart** (`limits.svg`): the table of expected versus measured with the status of each check.

![Limit enforcement](../bench/results/reference/limits.svg)

## Analysis
This section explains the tables in plain language. Anything that is a guess is labelled as a hypothesis.

### Startup
- **Plain process, `unshare` and minibox without limits are close.** Medians (AFTER run): `host_true` 1.913 ms, `unshare_ns` 3.948 ms, `minibox_nolimits` 4.064 ms. Creating the namespaces roughly doubles the time of a plain process, and minibox without limits is close to raw `unshare`: `pivot_root`, the mounts, the state file and the monitor process add little on top of namespace creation. The minimums agree (3.505 ms for `unshare_ns`, 3.496 ms for `minibox_nolimits`). The BEFORE run says the same (3.893 ms and 3.994 ms).
- **A caveat on that comparison.** The mean of `minibox_nolimits` (5.643 ms) is higher than that of `unshare_ns` (4.491 ms), and its standard deviation is larger (3.000 ms against 1.563 ms); its slowest run is 16.835 ms, against 9.639 ms for `unshare_ns`. In `startup_raw.csv`, 10 of the 30 `minibox_nolimits` runs took 6 ms or more, against 3 of 30 for `unshare_ns` (BEFORE run: 12 and 3): some runs fall in a slower group of about 8 ms. The cause was not investigated. The median hides this, which is why the tables also show the mean, p95 and max.
- **The old path (cgroup.procs migration) showed a stall.** In the BEFORE run, `minibox_limits` had a median of 32.124 ms, p95 33.828 ms and max 64.452 ms, against a median of 3.994 ms for `minibox_nolimits` in the same run; the minimum (3.506 ms) was as low as without limits. 17 of its 30 runs took 30 ms or more and 13 took under 10 ms. In the AFTER run the same old path, forced with `MINIBOX_NO_CLONE3=1` (`minibox_limits_migrate`), had a median of 16.529 ms, p95 34.005 ms and max 65.394 ms, minimum 3.555 ms. So the **p95 (33.828 and 34.005 ms) agrees between the two runs, but the median does not (32.124 and 16.529 ms).** The size of the median varies between runs, and it is a fragile statistic here: the runs form two groups, fast (under 10 ms) and slow (about 30 ms and more), and the median lands wherever half of the runs happen to fall. In the BEFORE run 13 runs were under 10 ms and 17 took 30 ms or more, so the median is inside the slow group. In the AFTER run 13 runs were under 10 ms, 4 were between 10 and 30 ms and 13 took 30 ms or more, so the median falls between the groups. That is an observation from `startup_raw.csv`; why the share of slow runs differs between two runs made hours apart is partly explained by the next bullets (the run order and the veth row) and partly not known.
- **Where the time goes (evidence) and why (hypothesis).** The benchmark alone only shows that the runs with limits are sometimes slow. `strace -f -ttt -T` on `minibox run` with limits showed that the time is spent in the parent's `write` of the child's PID to the container cgroup's `cgroup.procs`, and in no other system call. That is all the strace evidence shows: **where** the time goes when the stall happens, not **when** it happens or why. (The strace timings are not in the result folders and are not quoted here; strace itself changes the timing, because it slows every call, so it cannot tell how often the stall occurs.) Writing a PID to `cgroup.procs` *migrates* a running task into another cgroup. **Hypothesis, not proven:** the migration waits for a kernel grace period (for example an RCU grace period). The kernel side was not measured and no kernel tracing was done. The next bullet says when the stall happens.
- **When the stall happens: after a network-device teardown, not because of the limits (probe).** An earlier wording in this project, that limits cost about 30 ms of startup, was **wrong**. `bench/probe_attach_stall.sh` times `minibox run -- /bin/true` with `clone3` and with `MINIBOX_NO_CLONE3=1` (migration). The real output of its second run is kept, untouched, in `bench/results/analysis/attach_stall_probe.txt` (the first run was not saved and is not quoted).
  - *Limits alone, consecutive `--net none` runs* (seven combinations of memory, cpus and pids limits, 12 runs each): the `clone3` rows lie between 9 and 15 ms and the migration rows between 9 and 13 ms, except two values on the migration path: 40 ms (row `--net none --memory 64M`) and 32 ms (row `--net none --memory 256M --cpus 1 --pids 64`). Neither was preceded by a veth run. The `clone3` path had no such value (worst value 15 ms, row `--net none --cpus 0.5`). **This is a small sample (84 runs per path) and the cause of those two outliers is not known.** There is no systematic stall tied to a limit.
  - *A limited run right after a `--net veth` run* (10 per path; limits `--memory 64M --cpus 0.5 --pids 20`): `clone3` 10 to 18 ms, migration 24 to 39 ms. The stall is observed on the migration path only, after veth teardown.
  - *Mechanism, a hypothesis that was not verified:* network teardown starts a kernel grace period (RCU) that the cgroup migration has to wait for, whereas `clone3` creates the process inside the cgroup and does not wait. `clone3` avoided the stall in these measurements. Proof would need kernel tracing (future work: trace `synchronize_rcu` with ftrace or `perf` while a migration follows a veth teardown, and see whether the `write` to `cgroup.procs` sleeps in it).
  - *Why the benchmark's migrate row is inflated.* `bench/bench.py` shuffles all configurations, including `minibox_veth_limits`, into one random order, so some `minibox_limits_migrate` runs directly follow a veth run. In the AFTER run 6 migrate runs did: 5 took 32.965 to 34.094 ms and one 16.553 ms; the 4 `minibox_limits` (`clone3`) runs directly after a veth run took 4.092 to 8.590 ms. In the BEFORE run all 6 `minibox_limits` runs (old path) directly after a veth run took 32.517 to 33.746 ms. These come from `startup_raw.csv` (runs sorted by `order`). The share of such runs depends on the shuffle seed, which can account for part of why the old path's median differed between the runs (32.124 and 16.529 ms) while the p95 did not (not proven; see the next bullet). This interleaving is realistic for a busy host that also starts containers with networks, but the row is therefore **not "the cost of limits"**.
  - *What this does not explain.* In the AFTER run 8 of the 13 migrate runs of 30 ms or more did not directly follow a veth run: they came 2 to 9 runs after one, and 13 faster migrate runs were also 2 to 9 runs after one (counted in `startup_raw.csv`). So the veth explanation covers the runs directly after veth; for the others, a delayed effect or another cause is possible and was not investigated (the two isolated outliers of the probe are similar).
- **The fix, and the result.** The cgroup and its limits are created first and the container is created directly inside it with `clone3(CLONE_INTO_CGROUP)`, so no migration happens (`docs/src/container.md`). In the AFTER run, `minibox_limits` has a median of 3.770 ms, p95 8.449 ms and max 8.590 ms; `minibox_nolimits` in the same run has a median of 4.064 ms, p95 8.903 ms and max 16.835 ms. **Startup with limits is within noise of startup without limits**: the medians differ by less than the spread inside either configuration (standard deviations 1.999 ms and 3.000 ms), and with limits was even a little lower, which is noise, not a speed-up from limits. All 30 `minibox_limits` runs took under 10 ms (counted in `startup_raw.csv`), where the old path had 17 of 30 at 10 ms or more. The old path stays in the code, selected by `MINIBOX_NO_CLONE3=1` and used automatically when `clone3` is not available, and is benchmarked as `minibox_limits_migrate` so that both can be compared in one run.
- **veth (`minibox_veth_limits`): a network-setup floor remains.** BEFORE: median 165.379 ms, p95 194.342 ms, max 217.758 ms. AFTER: median 115.321 ms, p95 165.732 ms, max 166.501 ms. The earlier median **included the stall** (the veth row also uses limits), which is why it dropped. The **minimum was the same in both runs** (114.504 ms BEFORE, 114.412 ms AFTER), so a floor of about 114 ms remains that has nothing to do with the cgroup; it is far above every configuration with `--net none`. **This floor was not investigated.** Hypotheses, not measured: several `ip` commands are forked and executed one after another (each costs a fork, an exec and a netlink round trip), and the interfaces are moved between network namespaces, which may be slow in the kernel. In the AFTER run 26 of the 30 veth runs took under 140 ms and 4 took 140 to 190 ms; in the BEFORE run the split was 11, 17 and 2 runs under 140, 140 to 190 and 190 ms or more. The spread is observed, not explained.
- **`minibox exec` still joins the cgroup by migration and was not measured.** `exec` writes the PID to `cgroup.procs` (after entering the container's namespaces with `setns`), so it probably still pays the migration cost; no benchmark row covers `exec`. Future work: open the cgroup directory before `setns` and use `clone3(CLONE_INTO_CGROUP)` there too, then add a benchmark row.

### Memory
The memory results are the same in substance in the two runs. Values are not identical: the total per container was 485376, 437658 and 444006 bytes at k = 1, 5, 10 in the AFTER run, against 477184, 444826 and 476774 in the BEFORE run, and the extra cost of one more container was 439858 bytes (AFTER) against 478040 (BEFORE).
- **The comparison with a host `sleep` is not apples to apples.** The container's `sleep` is the busybox binary, a small program in the Alpine rootfs. The host's `sleep` is the glibc, dynamically linked GNU coreutils program, which maps more libraries. A host `sleep` using more memory (5.23 MiB at k=1, from 5487616 bytes) says nothing about the container runtime, so the host row is only a reference point for the order of magnitude.
- **The real minibox overhead** is the monitor process (a process on the host, outside the cgroup) plus the cgroup's own memory (`memory.current`) for each container. That sum is the "total / k" column: 0.46 MiB at k=1 (485376 bytes), 0.42 MiB at k=5 (437658 bytes), 0.42 MiB at k=10 (444006 bytes). The extra cost of each additional container (slope over k) is 439858 bytes, 0.42 MiB. These are small, nearly flat numbers.
- **Why the init process's PSS per container drops.** The table shows init PSS / k falling: 0.97 MiB at 1 container, 0.25 at 5, 0.15 at 10. PSS (proportional set size) divides each shared page among the processes that share it. All the containers' init processes map the same busybox binary and the same libc pages, so with one container that process pays for all of them, and with ten containers each pays a tenth. The first container carries more of the shared cost. This is expected, **not a measurement error**. The host `sleep` shows the same effect (5.23, 2.20, 1.74 MiB).
- **Init PSS is not added to the total.** The total uses the cgroup number plus the monitor. The init PSS is shown for information; it is not a subset of the cgroup number (at k=1 it is 0.97 MiB, while `memory.current` is 0.25 MiB per container). A likely reason, not verified, is that PSS includes shared file pages that the kernel charged to a different cgroup when they were first read.

### Limit enforcement
All eight checks have status OK in both runs; the conclusions are unchanged.
- **Memory.** With `--memory 64M` the workload was OOM-killed: exit code 137 (expected 137), `memory.events` `oom_kill` = 3 (expected at least 1), and `memory.peak` = 64.0 MiB (expected at most 1.10 x 64 MiB). The control run with the limit raised to 1G exited 0 with a peak of 192.9 MiB, so the workload really needs more than 64 MiB and it was the limit that stopped it. These four rows are identical in the two runs.
- **Processes.** With `--pids 20`, `pids.peak` was 20 (expected at most 20), and some of the 60 background children could not be forked (measured: yes). Identical in both runs.
- **CPU.** With `--cpus 0.5` the busy loop got 0.500 CPUs in the AFTER run (0.508 BEFORE; expected 0.40 to 0.60). The control with `--cpus 2` got 0.998 CPUs (0.997 BEFORE; expected above 0.80, one busy loop cannot use more than one CPU), so the loop itself is not slow.
- **What the counters prove.** `memory.events` is the kernel's own record that it killed a process for reaching the limit (`oom_kill`); the exit code 137 alone could also come from another SIGKILL. `memory.peak` is the highest memory use ever seen in the cgroup, so a peak of 64.0 MiB shows the limit held. `pids.peak` is the highest number of processes ever in the cgroup, so 20 shows `fork()` was refused beyond it. `cpu.stat` `usage_usec` is the CPU time actually consumed; divided by the wall time it gives the CPU share, which shows the throttle. The limits are enforced the same way with `clone3` as with the `cgroup.procs` path (`tests/clone3_test.sh` checks both).

### Limitations
- **WSL2 is a virtual machine**, so timings include the hypervisor and move with what Windows is doing.
- **The migrate row depends on the random order.** `minibox_limits_migrate` is inflated by the veth row it is shuffled with (see "When the stall happens"); its median moves with the share of its runs that follow a veth run. The interleaving mimics a busy host; it does not isolate the cost of the migration.
- **N = 30** runs per configuration (3 warm-ups discarded). p95 from 30 runs depends on one or two values, and the medians of the two-group configurations (the old path, veth) move with the share of slow runs.
- **Two separate runs, hours apart** (the BEFORE run on 2026-10-19T11:27:24+00:00, the AFTER run on 2026-10-19T12:33:44+00:00), with different minibox builds (see the machine lists). The before/after table compares two samples, not one randomised experiment; the AFTER run additionally contains the old path (`minibox_limits_migrate`) measured at the same time as the new one, which is the cleaner comparison.
- **Millisecond timings include Python `subprocess` overhead** (fork, exec, pipes), the same for every configuration but not zero. Differences between configurations are meaningful; absolute values are somewhat high.
- **Docker was not measured**, so there is no comparison with it.
- **Not measured:** `minibox exec` (and whether it is hit by the stall after veth teardown); the veth setup cost was not broken down; the kernel-side reason for the migration stall was not measured (the RCU explanation is a hypothesis); the probe is one saved run with 12 or 10 runs per row, and the cause of its two isolated slow migration runs is not known.
