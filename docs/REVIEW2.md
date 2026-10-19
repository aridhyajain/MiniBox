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
Reference run: N=30 per configuration, 3 warm-ups, interleaved in random order, on WSL2 (machine in `docs/BENCHMARKS.md`). Docker was **not measured** (not installed). Numbers below are from `bench/results/reference/`.

- **Startup (median):** plain process 1.716 ms, raw `unshare` 3.893 ms, minibox without limits 3.994 ms, minibox with limits 32.124 ms, minibox with veth and limits 165.379 ms. Rootfs setup adds little to namespace creation (0.101 ms over `unshare` at the median).
- **Memory:** the minibox overhead per container (monitor process + cgroup) is 0.46 MiB at 1 container, 0.42 at 5, 0.45 at 10; extra cost per container 478040 bytes. The host `sleep` row is a reference only (different binary and libc).
- **Limits:** all eight checks OK: OOM kill at 64 MiB (exit 137, `oom_kill` = 3, peak 64.0 MiB), `pids.peak` 20 with `--pids 20`, CPU 0.508 with `--cpus 0.5`.

Startup chart (**log scale** on the y axis; bar = median, whisker = p95):

![Startup time, log scale](../bench/results/reference/startup.svg)

Memory per container:

![Memory per container](../bench/results/reference/memory.svg)

Limit enforcement:

![Limit enforcement](../bench/results/reference/limits.svg)

Full tables and the analysis: `docs/BENCHMARKS.md`. How to explain each chart: `docs/VIVA_NOTES.md`, "Phase 6 results".

## 5. Known limitations and what to say if asked
| Limitation | What to say if asked |
|------------|----------------------|
| **Zombies are not reaped; `--init` is future work.** The user's command is PID 1 and never calls `wait()`, so orphans (for example after a killed `minibox exec`) stay as zombies and `pids.current` counts them. | "PID 1 must reap orphans and minibox's PID 1 is the user's program. Docker solves it with `--init` (tini), a tiny PID 1 that forwards signals and calls `wait()`. We documented it and left it as future work; zombies do not run, but `pids.current` counts them." |
| **No user namespace.** | "Container root is real root with full capabilities. A user namespace would map it to an unprivileged user. We left it out to keep the lab focused on the five namespaces and cgroups; it is the first thing to add for safety." |
| **Not a hard security boundary.** Shared kernel, root, no seccomp, no capability dropping. | "It is isolation for learning. A kernel bug or a root container can affect the host. Real runtimes add user namespaces, seccomp, capability drops and masked paths. That is also why the UI is bound to localhost with a token." |
| **veth startup cost.** Median 165.379 ms against 32.124 ms without veth. | "The likely reason is that the setup runs several `ip` commands as separate processes and moves interfaces between namespaces. That is the likely reason, not something we measured. Using netlink directly would probably be faster." |
| **Unexplained `minibox_limits` startup delay.** Median 32.124 ms against 3.994 ms without limits, but the minimum is 3.506 ms against 3.439 ms. | "We observed it and did not investigate it, so we do not claim a cause. Hypotheses: cgroup creation or removal cost, or waiting on teardown. Finding out needs timing inside minibox around each step." |
| **Benchmark caveats.** WSL2 VM, N=30, Python subprocess overhead, no Docker figure, results vary between runs. | "The differences between configurations are meaningful, the absolute values are slightly high and one run is a sample. Docker was not installed, so we did not compare against it." |
