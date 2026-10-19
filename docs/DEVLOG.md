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
