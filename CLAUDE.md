# minibox – project rules for Claude

minibox is a minimal container runtime in C (OS lab project) for WSL2/Ubuntu:
Linux namespaces (PID, UTS, mount, IPC, network), `pivot_root` into an Alpine
rootfs, cgroups v2 limits (memory/CPU/pids). Later: CLI (run/ps/stop/exec/logs),
Flask web UI, benchmarks.

## Permanent rules

### Git and privileges
- NEVER run any git command (no init, add, commit, push, branch, status, ...).
  The user handles all git/GitHub work manually.
- NEVER run commands with sudo. If something needs root, print the exact
  command for the user to run.

### Documentation (mandatory)
- Every source file (C, Python, shell, Makefile) must have a matching doc file
  in `docs/` that mirrors the source path, e.g. `src/cgroup.c` ->
  `docs/src/cgroup.md`, `scripts/check_env.sh` -> `docs/scripts/check_env.md`,
  `Makefile` -> `docs/Makefile.md`.
- Each doc must cover:
  1. Purpose of the file
  2. Every function: signature, inputs, return value, errors, side effects
  3. System calls used and why
  4. The OS concept it demonstrates
  5. How to test it
- Create or update the doc in the same step as the code change, never later.
- After finishing any task, append a dated entry to `docs/DEVLOG.md`
  describing what was done.

### C code style
- C11; compile with `-Wall -Wextra -Werror`.
- Check every syscall return value; report errors with `perror` or `strerror`.
- No global mutable state unless necessary.
- Small, focused functions with comments explaining the OS concept.

### General
- Keep things simple and readable; this will be explained in a viva.
- Temporary rootfs lives in `rootfs/` (git-ignored).

## Layout
`src/` C sources, `include/` headers, `scripts/` shell helpers, `ui/` Flask UI,
`tests/`, `benchmarks/`, `docs/` documentation, `rootfs/` Alpine rootfs (ignored).
