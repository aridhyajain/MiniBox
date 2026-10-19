# minibox architecture (planned)

## Goal
Run a command inside an isolated environment (a "container") built only from
Linux kernel primitives, without Docker.

## Isolation: namespaces (`clone`/`unshare`)
| Namespace | Flag | Effect |
|-----------|------|--------|
| PID | `CLONE_NEWPID` | Container sees its own process tree; first process is PID 1 |
| UTS | `CLONE_NEWUTS` | Own hostname |
| Mount | `CLONE_NEWNS` | Own mount table; allows `pivot_root` and private `/proc` |
| IPC | `CLONE_NEWIPC` | Own SysV IPC / POSIX message queues |
| Network | `CLONE_NEWNET` | Own network stack (loopback only at first) |

## Filesystem
The Alpine minirootfs in `rootfs/` becomes `/` via `pivot_root`; the old root is
unmounted, then `/proc` is mounted fresh.

## Resource limits: cgroups v2
Per-container directory under `/sys/fs/cgroup/minibox/<id>/` with
`memory.max`, `cpu.max`, `pids.max`; the child's PID is written to `cgroup.procs`.

## Planned components
1. **Runtime core (C)** – clone with namespaces, set up rootfs, apply cgroups, exec.
2. **CLI** – `run`, `ps`, `stop`, `exec` (`setns`), `logs`.
3. **State store** – per-container metadata (pid, limits, log path).
4. **Web UI (Python Flask)** – lists and controls containers via the CLI.
5. **Benchmarks** – startup time, memory/CPU limit enforcement, comparison to bare process.

## Phases
0. Setup (this phase) · 1. Namespaces · 2. Rootfs · 3. cgroups · 4. CLI · 5. Web UI · 6. Benchmarks
