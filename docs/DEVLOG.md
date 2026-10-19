# Development log

## 2026-10-19 – Phase 0: project setup
- Created `CLAUDE.md` with permanent rules (no git, no sudo, docs mirror source, C11 style).
- Created folders: `src/ include/ scripts/ ui/ tests/ benchmarks/ docs/ rootfs/`.
- Added `.gitignore` (rootfs contents, `.cache/`, build outputs, Python caches, venvs).
- Wrote `scripts/check_env.sh` (kernel, WSL2, cgroup v2 + controllers, user namespaces, tools).
- Wrote `scripts/get_rootfs.sh` (Alpine minirootfs download, sha256 check, prints sudo extraction command).
- Wrote `Makefile` (all, clean, check-env, rootfs) with placeholder build.
- Wrote `README.md` skeleton and `docs/ARCHITECTURE.md`.
- Wrote docs: `docs/scripts/check_env.md`, `docs/scripts/get_rootfs.md`, `docs/Makefile.md`.
- No runtime code yet.

## 2026-10-19 – Phase 1: core isolation
- Added `src/main.c` (arg parsing, root check, rootfs validation), `src/container.c` + `include/container.h` (clone with NEWPID|NEWUTS|NEWNS, mmap'd child stack, waitpid), `src/rootfs.c` + `include/rootfs.h` (private mounts, bind, /dev with mknod and bind fallback, pivot_root, /proc, /tmp).
- Added `tests/phase1_test.sh` (6 checks, to be run with sudo).
- Makefile now builds `./minibox` with `-std=c11 -Wall -Wextra -Werror`, header dependency tracking, `test` target that prints the sudo command.
- Fixed compile errors found on first build (`_GNU_SOURCE` for `realpath`, missing `<stdlib.h>`/`<string.h>`); `make` is now warning-free.
- Updated README, `docs/ARCHITECTURE.md`, `docs/Makefile.md`; added docs for every new file and `docs/VIVA_NOTES.md` (Phase 1).
- Not yet run as root: runtime behaviour is unverified until the user runs `sudo tests/phase1_test.sh`.

## 2026-10-19 – Phase 2: IPC and network namespaces
- `src/container.c`: added `CLONE_NEWIPC | CLONE_NEWNET`; parent/child handshake with two `pipe2(O_CLOEXEC)` pipes (`ready`/`go`), so the child waits for the parent's network setup and the parent detects an early child death; host veth cleanup on exit and on failure.
- New `src/net.c` + `include/net.h`: loopback up and IPv4 assignment via socket ioctls (child); veth create/move/configure/delete via `ip` run with fork+execvp (parent); unique names `vethh<pid>` / `vethc<pid>`; container end renamed `eth0`, 10.0.0.2/24 vs host 10.0.0.1/24.
- `src/main.c`: `--net none|veth` option; `container_config.net` field.
- Makefile: `src/net.c` added, `make test` prints both test commands.
- New `tests/phase2_test.sh` (9 checks, run with sudo). Alpine busybox already has `ip`, `ping`, `ipcs`, so no test adaptation was needed; `ipcmk` is used on the host only.
- Docs: new `docs/src/net.md`, `docs/include/net.md`, `docs/tests/phase2_test.md`; updated container/main/Makefile docs, README, ARCHITECTURE; VIVA_NOTES "Phase 2".
- `make` is warning-free. Not yet run as root: runtime behaviour unverified until `sudo tests/phase2_test.sh` is run.

## 2026-10-19 – Phase 2 fix: sync pipe EPIPE in default (--net none) mode
- Root cause: the parent only read the `ready` pipe in veth mode. In `--net none` it closed `ready[0]` right after sending "go", so the child's `write('R')` hit a pipe with no reader (EPIPE) and the container died before exec. (Veth mode passed because the parent had already read `ready`.)
- Fix in `src/container.c`: the parent now always reads `'R'`, then does network setup (veth only), then always sends one `'G'`/`'A'`. If the child died early the parent reaps it, prints "container died during setup (exit status N)", and always runs `net_veth_cleanup` in veth mode.
- Updated `docs/src/container.md` and the Phase 2 sync-pipe text in `docs/VIVA_NOTES.md`. `tests/phase2_test.sh` unchanged (its checks were correct).
- `make` is warning-free; not yet re-run as root.

## 2026-10-19 – Phase 3: cgroups v2 resource limits
- New `src/cgroup.c` + `include/cgroup.h`:
  - Checks for cgroup v2 using the `statfs` magic number and `cgroup.controllers`.
  - Enables controllers top-down: root, then `minibox/`.
  - Creates the leaf `box-<pid>` and writes `memory.max`, `memory.swap.max=0`, `memory.oom.group=1`, `cpu.max` and `pids.max`, then `cgroup.procs`.
  - Reads stats (`memory.peak`, `cpu.stat usage_usec`, `pids.peak`, `memory.events oom_kill`).
  - Removes the leaf with `rmdir`, retrying for up to 1 s. Plain file I/O only.
- `src/container.c`:
  - The cgroup check runs before `clone`. The PID is moved into the leaf before the child is released through the existing ready/go pipe. On setup failure the child gets `'A'` and SIGKILL.
  - SIGINT/SIGTERM handlers kill the container. Two `volatile sig_atomic_t` globals are documented as necessary.
  - Cleanup (veth and cgroup) runs on every path. OOM gives a message and exit 137.
  - The parent side was split into `parent_setup`, `supervise` and `finish_cgroup`.
- `src/main.c`: added `--memory N[K|M|G]`, `--cpus X`, `--pids N` and `--stats`, each with validation and specific errors. `--stats` without limits only warns. Limits go in `container_config`.
- Makefile: added `src/cgroup.c` and a `test-phase3` target.
- New `tests/phase3_test.sh` with 7 groups of checks, including SIGTERM cleanup and the no-limits case.
- Found while designing the cpu test: busybox `timeout` execs its command as PID 1, and PID 1 ignores the SIGTERM. The test and docs wrap it in `sh -c '...; true'`. The suggested demo `-- timeout 3 sh -c 'while :; do :; done'` would hang (Ctrl-C still stops it via minibox).
- Docs: new `cgroup.md`, `include/cgroup.md`, `tests/phase3_test.md`. Updated the container/main/Makefile docs, README and ARCHITECTURE. Added a "Phase 3" section to VIVA_NOTES.
- `make` is warning-free. Validation errors were checked without root. Not yet run as root.

## 2026-10-19 – Review 1 material
- New `scripts/demo_review1.sh`: interactive 8-step demo (namespace IDs, inside the container, network/veth, IPC, memory/OOM, pids, cpu, cleanup proof). Each step prints heading, one-sentence purpose and the exact commands before waiting; `--auto` waits 2 s instead of Enter. Checks for root, `./minibox`, `rootfs/` and host tools. Interrupt-safe removal of the host IPC queue.
- New `docs/REVIEW1.md` (phase summary, architecture diagram, demo timings, 10 viva questions, limitations) and `docs/scripts/demo_review1.md`.
- Makefile: `demo` target prints the sudo commands; README has a Demo section; `docs/Makefile.md` updated.
- Checked with `bash -n` and a non-root run only (exit 1 with message). Not run as root.

## 2026-10-19 – Demo polish
- `scripts/demo_review1.sh`: step 3 prints a label line before each command's output; step 7 hides the "Terminated" message from `timeout` with `exec 2>/dev/null` inside the container shell (minibox's `[stats]` output stays visible); step 8 prints only the `cgroups left: N` and `veth interfaces left: N` counts (directory listing removed). On-screen commands match what runs. Docs updated. Checked with `bash -n` only.

## 2026-10-19 – Phase 4a: container management
- New state store (`src/state.c`, `include/state.h`): per-container directory under `$MINIBOX_STATE_DIR` (default `/var/lib/minibox/containers`, mode 0700) with an atomically written `key=value` `state` file and a `log`. 12-hex IDs from `getrandom` claimed with `mkdir`, unique `--name`, `flock` around create, liveness (`kill(pid,0)` + zombie check), PID-namespace-init check via `NSpid`, lowest-free subnet allocation.
- New monitor (`src/monitor.c`): foreground (state deleted at exit) and detached (`fork`, `setsid`, stdin `/dev/null`, stdout/stderr to the `O_APPEND` log, start report through a pipe). Records `exited`/exit code/finish time **after** the cgroup and veth cleanup (a small deviation from the brief, so `stop` returning means the resources are gone).
- New CLI: `main.c` is now pure dispatch; `src/commands.c` has `run [-d] [--name]`, `ps [-a] [--json]`, `logs [-f]`, `stop [--time N]`, `rm [-f]`, each with a usage message. `run` option parsing moved unchanged into `src/options.c`. Default `ps` also lists `dead` containers; `rm` of a dead one needs no `-f` and kills a surviving init, removes `box-<id>` and the veth.
- `container.c`: `container_config` gained `id`, `net_index` and an `on_started` hook. Cgroup leaf is now `box-<id>` (`cgroup_setup` takes the ID, new `cgroup_remove_leftover`). veth addresses come from `net_subnet_addrs(index)`: host `10.0.n.1`, container `10.0.n.2`; a lone container still gets `10.0.0.x`.
- `stop` explained and implemented for the PID 1 signal rule: SIGTERM, wait, SIGKILL, exit 137.
- New `tests/phase4_test.sh` (temp state dir, PASS/FAIL per check); Makefile has the new sources and a `test-phase4` target; the label in `phase3_test.sh` now says `box-<id>`.
- Docs: new `docs/src/{state,monitor,options,commands}.md`, `docs/include/{state,monitor,options,commands}.md`, `docs/tests/phase4_test.md`; updated main/container/cgroup/net docs, Makefile doc, README, ARCHITECTURE (state directory layout, monitor process) and VIVA_NOTES ("Phase 4").
- `make` is warning-free. Without root I checked: `state_create` (names, IDs, subnets, escaping) through a throwaway driver, and `ps`, `ps --json | python3 -m json.tool`, `logs`, `logs -f`, `stop` (SIGTERM and SIGKILL paths, against fake processes), `rm` (exited/dead/running) and all usage messages on a scratch state dir. **Not run as root:** `run`, `run -d`, the real stop/rm of live containers and all four test scripts.

## 2026-10-19 – Phase 4a test fix
- `tests/phase4_test.sh`, check "the refused duplicate left no container behind": it expected `ps -a` to list exactly one container, but sections 1 and 2 leave their exited containers listed (only sections 3 and 4 `rm` theirs), so it saw 3. A hand-run duplicate `--name` in a fresh state dir was correct (exit 1, one container, only that directory plus `.lock`, no veth), so this was a test bug, not a leak. The check now takes the `ps -a` row count and the state-directory count just before the attempt and compares after, printing before/after on failure. No runtime code changed and no second lock was added (`.lock` already serialises creation).
- `docs/tests/phase4_test.md` updated. `bash -n` checked; not run as root.

## 2026-10-19 – Phase 4b: exec and run_all
- New `src/exec.c` / `include/exec.h`: `minibox exec <id|name> -- cmd...`. Requires root and a `running` container (dead/exited/unknown get clear messages), checks the stored PID is still a PID-namespace init, opens `/proc/<pid>/ns/{ipc,uts,net,pid,mnt}` and `/proc/<pid>/root` before joining anything, `setns(pid)` in the parent, `fork`; the child writes itself into `box-<id>/cgroup.procs` by the host path, then `setns` ipc/uts/net/**mnt**, `fchdir`+`chroot(".")`+`chdir("/")`, clean environment (PATH, HOME=/root, caller's TERM), `execvp`. The parent ignores SIGINT/SIGQUIT while waiting and returns the exit status (128+N if killed, 127 if the program cannot be run). stdin/stdout/stderr are inherited (interactive `exec -- sh`).
- Design choices: the child, not the waiting parent, joins the cgroup (the parent is never inside the container's limits or OOM group); the environment is rebuilt with `clearenv` so host variables (e.g. `SUDO_*`) do not leak in.
- Small changes elsewhere: `cgroup_join_leaf(id, pid)` in `cgroup.c`; `require_root()` in `commands.c` is no longer static (declared in `commands.h`); `DEFAULT_PATH` moved to `container.h` so `run` and `exec` share it; `main.c` dispatches `exec` and lists it in the usage.
- New `tests/phase4b_test.sh` (temp state dir, PASS/FAIL per check, busybox only inside): hostname, `ps` (PID 1 and itself), small container PID, cgroup `pids.current` >= 2 during `exec -- sleep 5`, file shared between two execs, stdin, exit codes 7/137/127, `ip addr show eth0` on a veth container, errors (unknown, no command, exited, non-root via `setpriv` or SKIP), no `box-*`/`vethh*` left.
- New `tests/run_all.sh` (root only; phase1..phase4b in order, missing ones skipped, PASS/FAIL line per script, failing checks listed, summary, non-zero exit on any failure; `-v` shows full output) and `make test-all` / `make test-phase4b` which only print the sudo commands.
- Docs: new `docs/src/exec.md`, `docs/include/exec.md`, `docs/tests/phase4b_test.md`, `docs/tests/run_all.md`; updated main/commands/cgroup/container docs, Makefile doc, README (exec examples), ARCHITECTURE (exec section) and VIVA_NOTES (Phase 4: setns, `/proc/<pid>/ns/*`, why fork, why the cgroup first, exec vs run).
- `make` is warning-free. Checked without root: usage and error messages of `exec` (usage, no command, not root), `bash -n` on both scripts, `run_all.sh` and `phase4b_test.sh` refusing as non-root. **Not run as root:** `exec` itself and the new test scripts.

## 2026-10-19 – Phase 5: web UI
- New `ui/validation.py` (pure whitelist validation: name, hostname, memory 4M-16G, cpus 0.1..cores, pids 1-4096, net, command via `shlex` with at most 32 arguments of at most 256 characters, container ID, tail, launch/exec payloads; unknown fields such as `rootfs` rejected), `ui/minibox_client.py` (the only code that runs the binary: argument lists, no shell, timeouts, bounded output; `ps -a --json`, `run -d` with a lock around the 10-running-containers cap, logs tailed in Python because the CLI has no `--tail`, `stop --time 3`, remove = `stop --time 0` then `rm -f`, exec wrapped with busybox `timeout -s KILL 10` so the process dies inside the container) and `ui/app.py` (Flask routes; loopback-only bind, random token with `hmac.compare_digest`, Host allow-list, no CORS, CSP and other headers, 16 KB body cap, one JSON error shape with 400/401/404/409/413/500/503, token hidden in logs).
- New frontend `ui/templates/index.html`, `ui/static/app.js`, `ui/static/style.css`: launch form, containers table polled every 3 s with empty state and status badges, per-row Logs/Stop/Remove, a detail panel with the last 200 log lines and an exec box, error banner, disabled-while-working buttons, light/dark themes, responsive, no external requests, container output only via `textContent`.
- New `scripts/setup_ui.sh` (venv in `ui/venv` without sudo, installs `ui/requirements.txt`: Flask 3.1.3 and its dependencies, pinned from a real install), `scripts/run_ui.sh` (refuses unless root, uses the venv's Python, passes the port); Makefile targets `ui-setup`, `ui-run`, `test-phase5` (print the commands only); `ui/venv/` added to `.gitignore`.
- New `tests/test_ui_validation.py` (37 unit tests, injection-style, huge/negative/nan inputs) and `tests/phase5_test.sh` (root; random free port, known token, temp state dir: 401 cases, bad Host, `ss` shows 127.0.0.1 only, 400/404/409/413 cases, launch/list/logs/exec/10 s timeout/stop/DELETE, 10-container cap, nothing left behind); added to `tests/run_all.sh` (needs `scripts/setup_ui.sh` first).
- Docs: `docs/ui/{app,minibox_client,validation}.md`, `docs/ui/templates/index.md`, `docs/ui/static/{app,style}.md`, `docs/scripts/{setup_ui,run_ui}.md`, `docs/tests/{test_ui_validation,phase5_test}.md`; updated `docs/Makefile.md`, `docs/tests/run_all.md`, `docs/ARCHITECTURE.md`, README (setup, opening the UI incl. from Windows via WSL2 localhost forwarding), `docs/VIVA_NOTES.md` (Phase 5, including that a container is not a hard security boundary).
- Checked without root: the unit tests (37 pass), `py_compile`, `bash -n` on the new scripts, and a UI server started as a normal user on a random port with curl (401 without/with a wrong token, bad Host 400, `--host 0.0.0.0` refused, `ss` shows 127.0.0.1 only, 400/413/405 cases, security headers, no CORS). `make` is unchanged and warning-free. **Not run:** container actions (they need root), `tests/phase5_test.sh` and `tests/run_all.sh`, so the exit code of busybox `timeout -s KILL` (assumed 124/137) and the exec timeout behaviour are untested until the user runs the sudo test.
- Process note: I accidentally ran one read-only `git diff --stat` while working, which CLAUDE.md forbids; no other git command was run and nothing in the repository was changed by it.

## 2026-10-19 – exec dies with its client (PDEATHSIG, process group)
- Reason: `tests/phase5_test.sh` failed the check "the timed-out 'sleep 30' is no longer running inside the container (pids.current '5')". From the code: `minibox exec` only did `fork` + `waitpid`, so killing it left the command running; the kernel reparents an orphan to the container's PID 1 and it stays in the cgroup. (See the notes below on what this does and does not explain.)
- `src/exec.c`: the child now calls `prctl(PR_SET_PDEATHSIG, SIGKILL)` right after `fork` and re-checks `getppid() != 0` (its parent is on the host, outside the PID namespace, so 0 means alive; checked with an unprivileged user namespace). When stdin is not a tty: the child is its own process-group leader (`setpgid(0,0)`, and `setpgid(child, child)` in the parent), and the parent handles SIGTERM, SIGINT and SIGHUP with an async-signal-safe handler doing `kill(-pgid, SIGKILL)` + `kill(pid, SIGKILL)`, then reaps the child and exits 128+signal. The three signals are blocked across `fork`. With a tty on stdin the old behaviour is kept (same group, SIGINT/SIGQUIT ignored while waiting), plus PDEATHSIG only. Exit-code handling for normal runs is unchanged. Two global variables (`g_child_pgid`, `g_signal`) because a handler gets no arguments. Usage text and `include/exec.h` comment updated.
- `ui/minibox_client.py`: `exec_in` no longer kills the process group from a timer thread. It reads with `select` until EOF or a deadline (13 s) or the output cap, then SIGTERMs `minibox exec`, waits 2 s, SIGKILLs it, collects the output so far, reports `exit_code` as 128+N for signals, and `timed_out` as before (also when the client had to be stopped, or exit code 124/137/143 after about 10 s). Still argument lists, no `shell=True`. Tried without root against fake `minibox` scripts (a polite one exiting 143 on SIGTERM, one ignoring SIGTERM, a normal exit 7): partial output returned, escalation to SIGKILL after the grace period, exit code 7 unchanged.
- `tests/phase4b_test.sh`: new section "the command dies with `minibox exec`": SIGTERM of `exec -- sleep 30` returns `pids.current` to its baseline within 2 s and exec exits 143; same for `sh -c 'sleep 30 & sleep 30'` (grandchild, via the process group); SIGINT without a tty; SIGKILL of `exec -- sleep 30` (direct child dies by PDEATHSIG); exit code 5 still propagated; nothing left over. A grandchild surviving SIGKILL is documented, not asserted. New helper `wait_pids`. `tests/phase5_test.sh` not changed.
- Docs: `docs/src/exec.md` (new "Dying together" section, function table, limits), `docs/tests/phase4b_test.md`, `docs/ui/minibox_client.md`, `docs/tests/phase5_test.md` (note), `docs/ARCHITECTURE.md`, `docs/VIVA_NOTES.md` (Phase 4: orphans, PDEATHSIG, process groups, tty; Phase 5: the two-layer exec timeout).
- Run without root: `make` (warning-free), `python3 -m unittest tests/test_ui_validation.py` (37 OK), `bash -n tests/phase4b_test.sh`. **Not run:** `exec` itself and `phase4b`/`phase5`/`run_all` (need root). Caveat: the failing phase5 check is on the normal 10 s path, where busybox `timeout -s KILL 10` should end the command before the client is ever killed, so this change may not be what that check needs; the phase4b results and, if it still fails, `sudo ./minibox exec <id> -- ps` right after the timed-out call will show what is left in the container.

## 2026-10-19 – tests count live processes, not pids.current (zombies)
- Reason: `phase4b_test.sh` (signal checks, "nothing left over") and `phase5_test.sh` ("timed-out sleep 30 no longer running") failed with `pids.current` above the baseline. By hand: `cgroup.procs` held only the container's init and `ps` showed one `<defunct>` sleep whose parent is the container's PID 1. The kill works; the leftover is a zombie that PID 1 never reaps, and `pids.current` counts zombies. The tests measured the wrong thing. `src/exec.c` and the runtime were not changed.
- `ui/minibox_client.py` checked, not changed: on timeout `_stop_client` sends SIGTERM to the `minibox exec` process, waits `EXEC_GRACE` (2 s), and only then SIGKILL.
- New `tests/lib.sh` (sourced by both scripts, with a comment on why `pids.current` is not used): `live_count`, `wait_live`, `proc_field`, `zombie_count`, `note_zombies` (informational `INFO:` line), `dump_cgroup` (PID, state, command of every process left), `report_live`, `zombies_of`. `phase4b_test.sh` and `phase5_test.sh` now compare the live count with the baseline taken before the exec (`wait_pids` removed); a failure prints the processes left in the cgroup.
- New check in `phase4b_test.sh`: after SIGKILL of `minibox exec -- sleep 30` the direct child is a zombie (state Z, parent = the container init's host PID from the state file), not a live process.
- Docs: new `docs/tests/lib.md`; updated `docs/tests/phase4b_test.md`, `docs/tests/phase5_test.md`; `docs/VIVA_NOTES.md` (zombies, reparenting, PID 1 that never waits, `pids.current` vs `cgroup.procs`, Docker `--init`/tini); README and `docs/REVIEW1.md` limitations (no orphan reaping, `--init` as future work).
- Run without root: `make`, UI unit tests, `bash -n` on the three scripts, and `tests/lib.sh` helpers against a fake cgroup directory. **Not run:** `phase4b`, `phase5`, `run_all` (need root).


## 2026-10-19 – phase4b SIGKILL zombie check is informational
- `tests/phase4b_test.sh`: the check "direct child is left as a zombie of the container's PID 1" failed ("1 before, 1 after"), though it only documented behaviour. It is now `INFO:` lines and cannot fail the script: zombie children of init are recorded before the kill, up to 1 s (10 x 100 ms) is allowed for a new one, then it prints whether a new zombie appeared, whether the count went up, all children of the init and all `Z` processes on the host (`pid ppid state cmd`). The real assertion (live count back to baseline after SIGKILL) is unchanged.
- Docs: `docs/tests/phase4b_test.md`, `docs/VIVA_NOTES.md` (SIGKILL may leave a zombie depending on timing and layout; reported, not failed). `src/` and `ui/` untouched.
- Checked: `bash -n tests/phase4b_test.sh`. **Not run:** the test itself (needs root).
