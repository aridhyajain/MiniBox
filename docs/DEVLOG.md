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
