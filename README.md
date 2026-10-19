# minibox

A minimal container runtime in C using Linux namespaces and cgroups v2 (OS lab project).

## Prerequisites

- WSL2 (Ubuntu) or native Linux with cgroup v2
- gcc, make, python3, wget or curl
- Run `make check-env` to verify

## Setup

```sh
make check-env   # verify the environment
make rootfs      # download Alpine minirootfs; prints a sudo tar command to run
```

## Build

```sh
make             # builds ./minibox (C11, -Wall -Wextra -Werror)
make test        # prints the command to run the tests
```

## Run

```sh
sudo ./minibox run [-d] [--name NAME] [--rootfs DIR] [--hostname NAME] [--net none|veth] \
                   [--memory N[K|M|G]] [--cpus X] [--pids N] [--stats] [--init] -- COMMAND [ARGS...]

# example: isolated shell, loopback only
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh

# example: also a veth link (container 10.0.n.2 <-> host 10.0.n.1; n = 0 for the first container)
sudo ./minibox run --rootfs rootfs --net veth -- /bin/sh

# example: resource limits (cgroups v2) and a usage summary after exit
sudo ./minibox run --rootfs rootfs --memory 64M --cpus 0.5 --pids 32 --stats -- /bin/sh
```

Defaults: `--rootfs rootfs`, `--hostname minibox`, `--net none`. The exit code is the command's exit code.
Isolation: PID, UTS, mount (with `pivot_root` into the Alpine rootfs), IPC and network namespaces.
Limits: `--memory` (bytes, K/M/G suffix; no swap), `--cpus` (cores, e.g. 0.5), `--pids` (max processes).
With any limit the container runs in `/sys/fs/cgroup/minibox/box-<id>`, removed on exit (also on Ctrl-C/SIGTERM).
Without limits no cgroup is created. An OOM-killed container exits with 137. `--stats` prints peak memory, CPU time, peak pids and OOM kills.
`--net none` gives only a loopback interface; `--net veth` adds a veth pair to the host (needs the host `ip` command).
Every veth container gets its own subnet `10.0.n.0/24` (the lowest free `n`), so several can run at once.

### Limits and start-up: `clone3` into the cgroup
With `--memory`, `--cpus` or `--pids`, minibox creates `/sys/fs/cgroup/minibox/box-<id>` and writes the limits first, then creates the container directly inside it with `clone3(CLONE_INTO_CGROUP)` (Linux 5.7+),
so the kernel never has to move a running process between cgroups (that move was the slow step; see Benchmarks below). If `clone3` is unavailable (old kernel, seccomp), minibox falls back to `clone()` and writing the PID to `cgroup.procs` and prints one note.
**Diagnostic switch:** `MINIBOX_NO_CLONE3=1` forces the fallback path (used by the benchmark row `minibox_limits_migrate` and by `tests/clone3_test.sh` to compare both). `minibox exec` still joins the cgroup by writing `cgroup.procs`, so `exec` still pays that cost.

### `--init`: a tiny PID 1 (opt-in)
```sh
sudo ./minibox run -d --init --name web -- sleep 300
sudo ./minibox exec web -- cat /proc/1/comm       # minibox-init  (the command is PID 2)
time sudo ./minibox stop web                       # quick: SIGTERM is forwarded, exit code 143
```
By default the command itself is the container's PID 1. With `--init` PID 1 is a small init (`src/minit.c`, shown as `minibox-init`) and the command runs as its child.
The init forwards SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1 and SIGUSR2 to the command, reaps every orphaned process with `waitpid(-1)` (no zombies), and exits
with the command's status (128+N if it was killed by signal N). So `stop` of a normal program ends with 143 right away instead of waiting for the grace period and
SIGKILL (137), and a program like busybox `timeout` can no longer hang the container by becoming PID 1. It costs one extra process per container; with `--pids` use at least 2.
`ps` shows `yes` in the INIT column and `ps --json` has `"init":true`. The web UI has a matching checkbox. Without `--init` nothing changes.

## Manage containers (Phase 4a)

Every container has an ID (12 hex characters) and a directory with its state in `/var/lib/minibox/containers/<id>/`
(override with `MINIBOX_STATE_DIR`). Commands that take `<id|name>` accept either.

```sh
# start in the background: prints only the ID; --name is optional but must be unique
sudo ./minibox run -d --name demo --memory 64M -- sh -c 'while :; do date; sleep 1; done'

sudo ./minibox ps                 # running (and dead) containers
sudo ./minibox ps -a              # also exited ones
sudo ./minibox ps --json          # JSON array, for scripts and the future web UI
# ID           NAME   STATUS   PID    INIT COMMAND                  CREATED   LIMITS
# 3fa9c01be274 demo   running  4121   -    sh -c while :; do da...  12s ago   mem=64M

sudo ./minibox logs demo          # print the output so far
sudo ./minibox logs -f demo       # follow until the container exits (Ctrl-C leaves it running)
sudo ./minibox stop demo          # SIGTERM, then SIGKILL after 5 s (--time N changes that)
sudo ./minibox rm demo            # delete the state and log of a stopped container
sudo ./minibox rm -f demo         # stop it first if it is running
```

Without `--init`, a plain `sleep` or `sh` as the container's PID 1 ignores SIGTERM (a PID-namespace init only gets signals it has a
handler for), so `stop` normally ends with SIGKILL and exit code 137. With `--init` it stops at once with 143. A foreground `minibox run` is registered while it
runs (so `ps` shows it) and its state is deleted when it ends. If a background container's monitor process is killed,
`ps` shows the container as `dead`; `rm` kills what is left of it and removes its cgroup and veth.

```sh
sudo tests/phase1_test.sh    # PID/UTS/mount/pivot_root checks
sudo tests/phase2_test.sh    # IPC/network/veth checks
sudo tests/phase3_test.sh    # cgroup limit checks
sudo tests/phase4_test.sh    # run -d / ps / logs / stop / rm checks
sudo tests/phase4b_test.sh   # exec checks
sudo bash tests/init_test.sh # --init checks (PID 1, reaping, exit codes, stop, limits, veth, exec)
sudo bash tests/clone3_test.sh # clone3 into the cgroup vs the cgroup.procs fallback (both paths, strace injection, timing)
sudo bash tests/run_all.sh   # all of the above, one PASS/FAIL line per script (make test-all prints this)
```

## Run a command in a running container (Phase 4b)

`exec` joins the namespaces and the cgroup of a running container (like `docker exec`). stdin/stdout/stderr are yours,
so a shell is interactive, and the exit status is the command's.

```sh
sudo ./minibox run -d --name demo --hostname boxy --memory 64M -- sleep 300
sudo ./minibox exec demo -- hostname           # boxy
sudo ./minibox exec demo -- ps                 # PID 1 is sleep, plus the ps itself
sudo ./minibox exec demo -- sh                 # interactive shell inside; 'exit' leaves it
sudo ./minibox exec demo -- sh -c 'exit 7'; echo $?   # 7
sudo ./minibox stop demo && sudo ./minibox rm demo
```

`exec` works only on a `running` container and needs root. Processes started with `exec` belong to the container's
cgroup, so its limits apply to them too.

## Web UI (Phase 5)

A local dashboard to launch, list, stop, remove, read logs of and `exec` into containers. It only calls the `minibox` CLI.

```sh
scripts/setup_ui.sh              # once, as your normal user (creates ui/venv, installs pinned Flask; no sudo)
sudo scripts/run_ui.sh 8080      # starts the server on 127.0.0.1:8080 (root, because minibox needs it)
# it prints:  minibox UI: http://127.0.0.1:8080/?token=<random token>   -> open that exact URL
```

- Open the printed URL in a browser. The page keeps the token in `sessionStorage`, removes it from the address bar and sends it as a
  header; without the token every `/api` call returns 401. The token changes every time the server starts.
- **From Windows:** WSL2 forwards `localhost`, so the same `http://127.0.0.1:8080/?token=...` URL opens in a normal Windows browser.
- The server listens on `127.0.0.1` only and refuses other hosts. **Never expose it** (no port forwarding to the network, no proxy):
  it can run commands as root, and a container is not a hard security boundary (shared kernel, no user namespace/seccomp).
- Tests: `python3 -m unittest tests/test_ui_validation.py -v` (no root) and `sudo tests/phase5_test.sh` (needs `scripts/setup_ui.sh` first).
  `make ui-setup` and `make ui-run` print the commands.

## Demo (Review 1)

```sh
sudo scripts/demo_review1.sh          # interactive, Enter between steps (~5 min)
sudo scripts/demo_review1.sh --auto   # 2 s pause per step, for recording
```

See `docs/REVIEW1.md` for the presentation guide and likely viva questions. `make demo` prints these commands.

## Benchmarks (Phase 6)
Startup time, memory per container and limit-enforcement checks, with CSV results and SVG charts. They need root, so make only prints the commands:
```sh
make bench-quick     # prints: sudo python3 bench/bench.py all --quick
make bench           # prints: sudo python3 bench/bench.py all
make charts          # no root: python3 bench/make_charts.py bench/results/latest
```
Method, how to read the charts and the limitations of the numbers: `docs/BENCHMARKS.md`.

### Results (reference run)
Two full runs (N=30 per configuration, WSL2, same machine) are kept: `bench/results/reference-before-clone3/` (before the `clone3` change) and `bench/results/reference/` (after). Docker was not measured (not installed). The story, in five steps:

1. **Measure.** In the first run, starting a container with limits had a median of 32.124 ms against 3.994 ms without limits, while its minimum (3.506 ms) was as low as without limits: a stall, not a constant cost.
2. **Strace.** `strace -f -ttt -T` showed the time is spent in the parent's write of the child's PID to the container's `cgroup.procs`, a cgroup *migration* of a running process, and in no other system call.
3. **Find.** So the delay is explained as far as *where* it happens. *Why* the write is slow is a hypothesis (a kernel grace-period wait of variable length); it was not measured in the kernel.
4. **Fix.** Create the cgroup and its limits first, then create the container directly inside it with `clone3(CLONE_INTO_CGROUP)`. The old path stays as a fallback (`MINIBOX_NO_CLONE3=1`, kernels older than 5.7) and as a benchmark row, `minibox_limits_migrate`.
5. **Re-measure.** With `clone3`, `minibox_limits` has a median of 3.770 ms, p95 8.449 ms and max 8.590 ms; `minibox_nolimits` in the same run has 4.064 ms, 8.903 ms and 16.835 ms: startup with limits is within noise of startup without limits. The old path in the same run: median 16.529 ms, p95 34.005 ms. Its median differs from the first run's (32.124 ms) because its runs form a fast and a slow group and the median falls wherever half of them land; the p95 agrees (33.828 ms before, 34.005 ms after).

With a veth network the median went from 165.379 ms to 115.321 ms (the earlier median included the stall), but the minimum is the same in both runs (114.504 ms before, 114.412 ms after): a network-setup floor remains that was **not investigated** (hypotheses: several `ip` commands are forked, interfaces are moved between namespaces). All eight limit checks passed in both runs. Details, the machine, the analysis and the limitations are in `docs/BENCHMARKS.md`.

Startup time before and after (milliseconds; printed by `python3 bench/make_report_tables.py --compare bench/results/reference-before-clone3 bench/results/reference`):

| Configuration | Run | n | median (ms) | mean (ms) | p95 (ms) | max (ms) |
|---|---|---|---|---|---|---|
| minibox_nolimits | before | 30 | 3.994 | 5.733 | 9.242 | 9.654 |
| minibox_limits | before | 30 | 32.124 | 21.922 | 33.828 | 64.452 |
| minibox_nolimits | after | 30 | 4.064 | 5.643 | 8.903 | 16.835 |
| minibox_limits | after | 30 | 3.770 | 4.836 | 8.449 | 8.590 |
| minibox_limits_migrate | after | 30 | 16.529 | 19.888 | 34.005 | 65.394 |
| minibox_veth_limits | before | 30 | 165.379 | 150.778 | 194.342 | 217.758 |
| minibox_veth_limits | after | 30 | 115.321 | 122.097 | 165.732 | 166.501 |

Startup of the other configurations in the after run (`bench/results/reference/startup_summary.csv`):

| Configuration | n | min (ms) | median (ms) | mean (ms) | p95 (ms) | stdev (ms) |
|---|---|---|---|---|---|---|
| host_true | 30 | 1.391 | 1.913 | 1.913 | 2.347 | 0.279 |
| unshare_ns | 30 | 3.505 | 3.948 | 4.491 | 8.708 | 1.563 |
| docker | not measured: docker CLI not found | - | - | - | - | - |

Caveats: WSL2 virtual machine, N=30, two separate runs hours apart, timings include Python `subprocess` overhead. `minibox exec` was not measured.

Startup chart (bar = median, whisker = p95; **the y axis is a log scale**):

![Startup time, log scale](bench/results/reference/startup.svg)

Memory per container (1, 5 and 10 containers running `sleep 300`):

![Memory per container](bench/results/reference/memory.svg)

Limit enforcement (expected versus measured):

![Limit enforcement](bench/results/reference/limits.svg)

Startup chart, memory chart and limits chart above are from the after run (`bench/results/reference/`). Regenerate the tables with `python3 bench/make_report_tables.py bench/results/reference`.

## Documentation

See `docs/ARCHITECTURE.md`, `docs/VIVA_NOTES.md`, `docs/DEVLOG.md`, `docs/BENCHMARKS.md`, and the review guides `docs/REVIEW1.md` and `docs/REVIEW2.md`. Every source file has a matching doc under `docs/`.

## Known limitations
- Zombies: solved opt-in with `--init`; the default is still no reaping. Without `--init` the user's command is PID 1 and never calls `wait()`, so orphaned
  processes that die, for example after a killed `minibox exec`, stay as zombies and a long-running container can accumulate them. They do not run;
  `cgroup.procs` does not list them, but `pids.current` counts them. Use `run --init` (a tiny reaping PID 1, like Docker's `--init`/`tini`) for containers that need it.
- veth network setup costs a lot (a floor of about 114 ms per start, `bench/results/reference/`); the cause was not investigated.
- `minibox exec` still joins the container's cgroup by writing `cgroup.procs` (migration); its start-up time was not measured.
- No user namespace: container root is real root with full capabilities.
- Not a hard security boundary: shared kernel, no seccomp, no capability dropping. Isolation for learning.
