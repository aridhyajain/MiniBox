# bench/bench.py

## Purpose
Runs the three benchmark areas against the `minibox` binary and writes the results to `bench/results/<timestamp>/`, then copies that directory to `bench/results/latest/`.
It refuses to run unless root (minibox needs root). Standard library only. It uses its own temporary `MINIBOX_STATE_DIR`, so it cannot see or touch the user's containers, and
a `finally` block (also reached on Ctrl-C and SIGTERM) removes every container it started and then checks that no `box-*` cgroup, `vethh*` interface or state entry is left (`CLEANUP: OK` / `CLEANUP: FAIL` line, exit code 1 on FAIL). If such leftovers exist **before** the run, it refuses to start.
When started through `sudo`, result files are `chown`ed back to the invoking user (`SUDO_UID`/`SUDO_GID`).

## Usage
`sudo python3 bench/bench.py {startup|memory|limits|all} [--quick] [--runs N] [--seed S] [--out BASE_DIR]`
`--quick`: N=5, 1 warm-up, 2 s CPU test. Default: N=30, 3 warm-ups, 5 s CPU test. `--runs` overrides N. `--seed` fixes the random run order (default: time, recorded in `system.json`).

## Output files
`startup_raw.csv` (config, order, ns, returncode: every measured run), `startup_summary.csv` (n, failed, min/median/mean/p95/max/stdev in ms, status, note), `memory.csv`, `memory_incremental.csv`, `limits.csv`, `system.json`, `summary.txt` (what was printed).
`system.json`: date, kernel, CPU model and cores, RAM, WSL flag, Python, minibox build time and sha256 (there is no version constant in the code, so the build time is the version string), docker version, the options used.

## Functions
| Function / method | Inputs | Returns | Errors / side effects |
|-------------------|--------|---------|-----------------------|
| `Bench(args)` | parsed arguments | holder of paths, options and cleanup lists | creates a temp state directory |
| `Bench.say(line)` | text | – | prints and records the line for `summary.txt` |
| `Bench.write_csv(name, header, rows)` | – | path | writes into the run directory |
| `Bench.mb_run_args(extra, command, detached)` | minibox flags, command | argv list for `minibox run --rootfs ROOTFS ...` | – |
| `Bench.start_detached(extra, command)` | – | 12-hex container ID | `RuntimeError` if `run -d` fails |
| `Bench.state(cid)` / `wait_running(cid)` | ID | dict from the `state` file / the dict once `running` | `RuntimeError` after 15 s |
| `Bench.container_ids()` | – | IDs in the temp state dir | – |
| `Bench.kill_container(cid)` | ID | – | fast kill: writes `1` to `box-<id>/cgroup.kill`, then `SIGKILL` of the init PID from the state file (for boxes without a cgroup), then `minibox rm -f` (20 s timeout). Needed because a PID-1 `sleep 300` ignores SIGTERM, so `rm -f`/`stop` would wait out the whole grace period. `stop --time 0` exists but `rm -f` has no such option, so `src/` is not touched |
| `Bench.cleanup()` | – | list of what was cleaned | `kill_container` for every ID, kills host `sleep`s, `docker rm -f` (30 s timeout) for ours; never raises |
| `Bench.fail(kind, what)` | `"ERROR"`/`"TIMEOUT"`, text | – | prints `KIND: what`, remembers it so the exit code is non-zero; never raises |
| `Bench.flush_limits()` | – | – | rewrites `limits.csv` from `limit_rows` (called after every check, so a crash keeps earlier rows) |
| `Bench.leftovers()` | – | list of remaining cgroups, `vethh*` interfaces, state entries | runs `ip -o link show` |
| `Bench.detect_docker()` | – | sets `docker_ok`, `docker_note` | docker only if the CLI exists and `docker image inspect alpine` succeeds; never pulls |
| `proc_mem_bytes(pid)` | host PID | `(bytes, "pss"/"rss"/"none")` | PSS from `/proc/<pid>/smaps_rollup`, fallback `VmRSS` |
| `parent_pid(pid)`, `pid_cgroup_dir(pid)`, `cgroup_memory_current(path)` | – | PPID (docker shim), cgroup directory of a PID, `memory.current` or `None` | – |
| `timed(argv, env, timeout=20)` | command | `(elapsed_ns, returncode)`; returncode `-999` on timeout | `time.perf_counter_ns` around `subprocess.run`, stdio to `/dev/null`; a timeout is recorded as `TIMEOUT:`, the container killed, the run continues |
| `startup_configs(b)` | – | ordered dict name to (argv or `None`, note) | `None` = not measured |
| `bench_startup(b)` | – | – | warm-ups, shuffled interleaved runs, two CSVs |
| `measure_minibox_memory`, `measure_host_memory`, `measure_docker_memory` `(b, k)` | k containers | `[cgroup_total, init_total, monitor_or_shim_total, total, kinds]` | start k `sleep 300`, sample, caller cleans up |
| `bench_memory(b)` | – | – | k = 1, 5, 10; per-container = total / k; slope via `stats.incremental_cost` |
| `memory_hog_workload()`, `memory_control_workload()`, `pids_workload(n)`, `cpu_workload(seconds)`, `all_workloads()` | – | the `sh -c` strings run in containers | pure; every one ends with `; exit 0` (see below); unit tested |
| `run_with_stats(b, extra, shell_cmd, timeout=20)` | minibox flags, `sh -c` command, seconds | dict: rc, wall_us, stdout, stderr, memory_peak, cpu_usec, pids_peak, oom_kill | parses minibox's `[stats]` lines; on timeout kills the containers and re-raises `TimeoutExpired` |
| `add_check(b, name, expected, measured, tolerance, ok, detail)` | – | – | appends a row with status `OK`/`FAIL`, flushes `limits.csv`, prints `OK:`/`FAIL:` line |
| `limit_group(b, names, fn)` | check names, function running them | – | runs one group; `TimeoutExpired` or any exception becomes `TIMEOUT`/`ERROR` rows for the group's missing checks, records the event, cleans up, continues |
| `bench_limits(b)` | – | – | 8 checks in 5 independent groups |
| `system_info(b, sections)`, `chown_tree`, `publish_latest`, `main` | – | – | see above |

## Configurations (A) and workloads (B, C)
1. `host_true`: `/bin/true` on the host. 2. `unshare_ns`: `unshare --fork --pid --mount-proc --uts --ipc --net true`. 3. `minibox_nolimits`: `--net none`. 4. `minibox_limits`: `--net none --memory 256M --cpus 1 --pids 64`. 5. `minibox_veth_limits`: same with `--net veth`. 6. `docker`: `docker run --rm --network none alpine /bin/true`, or "not measured".
B: `--memory 1G` is given so a cgroup (and `memory.current`) exists; the monitor process is outside that cgroup, so the total is `memory.current + monitor PSS`. PSS of the init is part of `memory.current` already, so it is shown but not added.
C: memory: `awk 'BEGIN{s="x"; while(1) s=s s}'` with `--memory 64M`; control: a bounded awk (2^27 characters) with `--memory 1G`. pids: 60 background `sleep 5` with `--pids 20`; fork failure = the script did not reach `started_all`, or stderr mentions a fork error. cpu: `timeout N sh -c 'while :; do :; done'` with `--cpus 0.5` (ratio = `usage_usec` / wall time of the whole `minibox run`, so setup time makes it slightly low); control `--cpus 2` (or the core count if smaller), expects > 0.8.

## Robustness rules
- **Every external call has a timeout** (`CALL_TIMEOUT` = 20 s; 30 s for the memory workloads; CPU test length + 15 s). On timeout or error the failure is recorded (`TIMEOUT`/`ERROR`), the container is killed at once, and the run **continues**. A failing section does not abort the others; `main` still writes `system.json`, `summary.txt`, the CSVs and `latest/`. The exit code is 1 if any `ERROR`/`TIMEOUT` or leftover happened.
- **`limits.csv` status column**: `OK`, `FAIL` (the kernel behaved differently from expected), `ERROR` (the step crashed), `TIMEOUT` (no answer in time). A row is written even for a broken check.
- **Workloads keep the shell as PID 1.** busybox `sh -c "cmd"` execs the last command, so it would become the container's init. A PID-namespace init ignores signals sent from inside its namespace unless it installed a handler, so e.g. `timeout` (which ends by signalling itself) never exits and the container hangs. Every workload therefore ends with `; exit 0`. The `sleep 300` of part B is meant to be PID 1 (it is idle on purpose, and is removed through the fast kill path).
- **Signals**: SIGINT and SIGTERM raise `KeyboardInterrupt`; the `finally` block cleans every container, prints `INTERRUPTED: cleaned N item(s): ...`, removes the temporary state directory, skips publishing `latest/`, and the exit code is 130.
- **Timing output**: `Section A (startup) took X s`, same for B and C, and `Total time X s`.

## System calls used and why
Through Python and the tools it starts: `fork`/`execve`/`wait` (`subprocess`) to run the commands being timed; `clock_gettime(CLOCK_MONOTONIC)` (`perf_counter_ns`) for timing; reads of `/proc/<pid>/smaps_rollup`, `/proc/<pid>/status`, `/proc/<pid>/cgroup` and `/sys/fs/cgroup/...` for memory; `kill` (SIGKILL of the init) and writing `cgroup.kill` in cleanup, `wait`; `geteuid` for the root check; `chown` for results.

## OS concept
Measuring a container runtime: process creation cost, namespace and cgroup setup cost, memory accounting (RSS vs PSS vs cgroup `memory.current`), and the cgroup v2 controllers' counters (`memory.events`, `memory.peak`, `pids.peak`, `cpu.stat`).

## How to test
`sudo python3 bench/bench.py all --quick` (about 1 minute), or `sudo bash tests/phase6_test.sh`. Without root it prints the sudo command and exits 1. The statistics are unit tested (`tests/test_bench_stats.py`). Methodology and limits of the numbers: `docs/BENCHMARKS.md`.
