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
