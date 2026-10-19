# Review 2 guide

## 1. Project scope (all phases)
| Phase | What | Files |
|-------|------|-------|
| 0 Setup | Skeleton, environment check, Alpine rootfs download, Makefile | `CLAUDE.md`, `scripts/check_env.sh`, `scripts/get_rootfs.sh`, `Makefile` |
| 1 Isolation | `clone()` with PID, UTS, mount namespaces; PID 1 in the container; `pivot_root` into Alpine | `src/main.c`, `src/container.c`, `src/rootfs.c` |
| 2 IPC + network | IPC and network namespaces; loopback; optional `--net veth` | `src/net.c` |
| 3 Cgroups v2 | `--memory`, `--cpus`, `--pids`, `--stats`; OOM exit 137; cleanup on every exit path | `src/cgroup.c` |
| 4a Management | `run -d`, `ps`, `logs`, `stop`, `rm`; state on disk; a monitor process per container | `src/commands.c`, state store |
| 4b Exec | `minibox exec` joins a running container with `setns` (and its cgroup) | `src/exec.c` |
| 5 Web UI | Local Flask dashboard that drives the CLI; token, Host check, loopback only | `ui/` |
| 6 Benchmarks | Startup time, memory per container, limit-enforcement checks; CSVs, SVG charts, report tables | `bench/`, `docs/BENCHMARKS.md` |

Review 1 covered phases 0 to 3 (`docs/REVIEW1.md`). Docs for every file are under `docs/`; the full history is in `docs/DEVLOG.md`.

## 2. Architecture
```
   browser ──HTTP (127.0.0.1 + token)──> ui/app.py (Flask)
                                            |  calls the CLI, never shell=True
                                            v
   terminal ──────────────────────────> ./minibox  run | ps | logs | stop | rm | exec
                                            |
                      state: /var/lib/minibox/containers/<id>/  (state file, log)
                                            |
                 monitor process (host) ── clone(NEWPID|NEWUTS|NEWNS|NEWIPC|NEWNET)
                      |                         |
                      |                    container init (PID 1): pivot_root into Alpine
                      |                         |
                      |  cgroup /sys/fs/cgroup/minibox/box-<id>  (memory, cpu, pids)
                      |  veth pair (optional, via the host `ip` command)
                      v
        bench/bench.py ──> CSV + system.json ──> make_charts.py (SVG), make_report_tables.py (tables)
```
Namespaces decide what the process can see; cgroups decide what it can use. Details: `docs/ARCHITECTURE.md`.

## 3. Demo steps
Before presenting: `make`, `make rootfs` (once), `scripts/setup_ui.sh` (once, no sudo).

**CLI (about 3 minutes)**
```sh
sudo ./minibox run -d --name demo --memory 64M --cpus 0.5 --pids 20 -- sleep 300
sudo ./minibox ps                      # shows demo, running, with its limits
sudo ./minibox exec demo -- ps         # PID 1 is sleep: own PID namespace
sudo ./minibox logs demo
sudo ./minibox stop demo && sudo ./minibox rm demo
```
Say: the same cgroup and namespaces apply to `exec` processes; `stop` ends with SIGKILL (137) because a PID 1 with no signal handler ignores SIGTERM.

**UI (about 2 minutes)**
```sh
sudo scripts/run_ui.sh 8080            # open the printed URL with ?token=...
```
Launch a container with a memory limit, show it in the list, read its logs, run `hostname` through exec, stop and remove it. Say: the UI only calls the CLI, binds to 127.0.0.1 and needs the token.

**Benchmark (about 2 minutes; do not run the full benchmark live, it takes several minutes)**
Show the results of the reference run instead of re-running:
```sh
python3 bench/make_report_tables.py bench/results/reference    # tables from the CSV files
# open bench/results/reference/startup.svg, memory.svg, limits.svg in a browser
```
Optional live check: `sudo python3 bench/bench.py all --quick` (N=5, about 1 minute). Dress rehearsal: `sudo bash tests/run_all.sh`.

## 4. Results
Two runs, N=30 per configuration, 3 warm-ups, interleaved in random order, on WSL2 (machine in `docs/BENCHMARKS.md`): `bench/results/reference-before-clone3/` (before the clone3 change) and `bench/results/reference/` (after). Docker was **not measured** (not installed). All numbers below are from those folders.

**The story: measure, strace, find, fix, re-measure.**
1. *Measure.* First run: startup with limits had a median of 32.124 ms against 3.994 ms without limits, but a minimum of 3.506 ms, as low as without limits. A stall, not a constant cost.
2. *Strace.* `strace -f -ttt -T` showed the time is in the parent's write of the child's PID to `cgroup.procs` (a cgroup migration of a running task) and in no other syscall.
3. *Find.* strace shows **where** the time goes when the stall happens (and strace itself changes timing). **When** it happens was found later with a probe: after veth teardown, not because of the limits (see below). **Why** is a hypothesis (a grace-period wait in the kernel, started by network teardown); not measured.
4. *Fix.* Create the cgroup and write the limits first, then create the container inside it with `clone3(CLONE_INTO_CGROUP)`. The old path is kept (kernels before 5.7, comparison, `MINIBOX_NO_CLONE3=1`).
5. *Re-measure.* `minibox_limits` median 3.770 ms, p95 8.449 ms, max 8.590 ms; `minibox_nolimits` in the same run 4.064 ms, 8.903 ms, 16.835 ms: within noise. The forced old path in the same run: median 16.529 ms, p95 34.005 ms, max 65.394 ms.

| Configuration | Run | n | median (ms) | mean (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|---|---|
| minibox_nolimits | before | 30 | 3.994 | 5.733 | 9.242 | 9.654 |
| minibox_limits | before | 30 | 32.124 | 21.922 | 33.828 | 64.452 |
| minibox_nolimits | after | 30 | 4.064 | 5.643 | 8.903 | 16.835 |
| minibox_limits | after | 30 | 3.770 | 4.836 | 8.449 | 8.590 |
| minibox_limits_migrate | after | 30 | 16.529 | 19.888 | 34.005 | 65.394 |
| minibox_veth_limits | before | 30 | 165.379 | 150.778 | 194.342 | 217.758 |
| minibox_veth_limits | after | 30 | 115.321 | 122.097 | 165.732 | 166.501 |

- **Median of the old path differs between runs** (32.124 ms in the first run, 16.529 ms in the second), the p95 does not (33.828 ms and 34.005 ms). Its runs form a fast group (under 10 ms) and a slow group (about 30 ms and more) and the median falls wherever half of them land.
- **The stall is not a cost of the limits (probe, `bench/probe_attach_stall.sh`, output in `bench/results/analysis/attach_stall_probe.txt`).** (An earlier wording "limits cost about 30 ms" was wrong.) With consecutive `--net none` runs and seven limit combinations the two paths are alike (`clone3` 9 to 15 ms, migration 9 to 13 ms), except two isolated slow migration runs (40 ms and 32 ms, one in each of two rows of 12, no veth run before them; none on `clone3`, worst 15 ms). Small sample; cause of those two unknown. A limited run right after a `--net veth` run took 24 to 39 ms on the migration path and 10 to 18 ms with `clone3`. So the stall is **observed after veth teardown**; the mechanism (network teardown starts an RCU grace period that the migration waits for, `clone3` does not) is a **hypothesis**; `clone3` avoided the stall in the measurements. The benchmark shuffles a veth row in with the others, so the migrate row is **inflated** by that (in the after run 5 of the 6 migrate runs directly after a veth run took 32.965 to 34.094 ms); this resembles a busy host. It explains only part of the slow runs: 8 of 13 runs of 30 ms or more were not directly after a veth run.
- **Other startup medians (after run):** plain process 1.913 ms, raw `unshare` 3.948 ms, minibox without limits 4.064 ms: rootfs setup adds little to namespace creation. veth with limits: 115.321 ms (165.379 ms before, which included the stall); the minimum is the same in both runs (114.504 ms before, 114.412 ms after), so a veth setup floor remains, not investigated.
- **Memory (after run):** overhead per container (monitor process + cgroup) 0.46 MiB at 1 container, 0.42 at 5, 0.42 at 10; extra cost per container 439858 bytes. The host `sleep` row is a reference only (different binary and libc). Init PSS per container falls with more containers (0.97, 0.25, 0.15 MiB) because PSS splits shared pages among the processes sharing them.
- **Limits:** all eight checks OK in both runs: OOM kill at 64 MiB (exit 137, `oom_kill` = 3, peak 64.0 MiB), `pids.peak` 20 with `--pids 20`, CPU 0.500 with `--cpus 0.5` (0.508 in the first run).

Startup chart of the after run (**log scale** on the y axis; bar = median, whisker = p95):

![Startup time, log scale](../bench/results/reference/startup.svg)

Memory per container:

![Memory per container](../bench/results/reference/memory.svg)

Limit enforcement:

![Limit enforcement](../bench/results/reference/limits.svg)

Full tables and the analysis: `docs/BENCHMARKS.md`. How to explain each chart: `docs/VIVA_NOTES.md`, "Phase 6 results".

## 5. Known limitations and what to say if asked
| Limitation | What to say if asked |
|------------|----------------------|
| **Zombies are not reaped by default; solved opt-in with `--init`.** Without the flag the user's command is PID 1 and never calls `wait()`, so orphans (for example after a killed `minibox exec`) stay as zombies and `pids.current` counts them. | "PID 1 must reap orphans and by default minibox's PID 1 is the user's program. With `minibox run --init` PID 1 is a tiny init (like Docker's `--init`/tini) that forwards signals and calls `waitpid(-1)` in a loop, so there are no zombies and `stop` gives 143. It is opt-in because it costs one extra process; the default is still no reaping." |
| **No user namespace.** | "Container root is real root with full capabilities. A user namespace would map it to an unprivileged user. We left it out to keep the lab focused on the five namespaces and cgroups; it is the first thing to add for safety." |
| **Not a hard security boundary.** Shared kernel, root, no seccomp, no capability dropping. | "It is isolation for learning. A kernel bug or a root container can affect the host. Real runtimes add user namespaces, seccomp, capability drops and masked paths. That is also why the UI is bound to localhost with a token." |
| **veth startup cost not investigated.** Floor of about 114 ms per start (minimum 114.504 ms and 114.412 ms in the two runs; median 115.321 ms in the after run) against 3.770 ms with limits and `--net none`. | "We measured it and did not investigate it. Hypotheses, not measured: several `ip` commands are forked and executed one after another, and the interfaces are moved between network namespaces. Doing it through netlink directly would probably be faster; it is future work. We fixed the cgroup stall first because it was the larger, understood problem." |
| **`minibox exec` still joins the cgroup by migration and was not measured.** | "`exec` writes its PID to `cgroup.procs`, which is the slow path we found for `run`, so it probably has the same stall. We did not benchmark `exec`. The fix would be the same: open the cgroup directory before `setns` and use `clone3(CLONE_INTO_CGROUP)`. Future work." |
| **Benchmark caveats.** WSL2 VM, N=30, two separate runs hours apart, Python subprocess overhead, no Docker figure, medians of two-group configurations move between runs. | "The differences between configurations are meaningful, the absolute values are slightly high and each run is a sample. The old path's median was 32.124 ms in one run and 16.529 ms in the other, while its p95 stayed near 34 ms, so we quote p95 and max next to the median. Docker was not installed, so we did not compare against it. The kernel-side reason for the old stall is a hypothesis; strace only shows which write is slow, and a later probe showed it happens after veth teardown, not because of the limits." |
