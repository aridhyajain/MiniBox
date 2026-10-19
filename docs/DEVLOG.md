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
