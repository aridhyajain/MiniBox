# minibox architecture

## Goal
Run a command inside an isolated environment (a "container") built only from
Linux kernel primitives, without Docker.

## Status
- **Phase 1 (implemented):** PID, UTS and mount namespaces; `pivot_root` into the Alpine rootfs.
- Later: IPC and network namespaces, cgroups v2, CLI, Flask UI, benchmarks.

## Modules (Phase 1)
```
src/main.c        parse args, check root, validate rootfs
    |  container_run(cfg)
    v
src/container.c   mmap stack, clone(NEWPID|NEWUTS|NEWNS), waitpid
    |  (in child)  rootfs_setup(rootfs)
    v
src/rootfs.c      private mounts, bind, /dev, pivot_root, /proc, /tmp
```
Headers: `include/container.h`, `include/rootfs.h`.

## Child setup order (inside the new namespaces; child is PID 1)
1. `sethostname(hostname)` – UTS ns
2. `mount(/, MS_REC|MS_PRIVATE)` – stop mount propagation to the host
3. bind-mount rootfs onto itself – make it a mount point
4. tmpfs on `<rootfs>/dev` + `mknod` null/zero/random/urandom/tty (fallback: bind-mount host devices; must happen before the pivot)
5. `pivot_root(rootfs, rootfs/.pivot_old)`, `chdir("/")`, `umount2(/.pivot_old, MNT_DETACH)`, `rmdir`
6. mount `proc` on `/proc`, tmpfs on `/tmp`
7. `chdir("/")`, set `PATH`/`HOME`, `execvp(command)`

The parent only calls `waitpid` and returns the child's exit code (128+N for signal N).

## Isolation: namespaces (`clone`/`unshare`)
| Namespace | Flag | Effect | Status |
|-----------|------|--------|--------|
| PID | `CLONE_NEWPID` | Own process tree; first process is PID 1 | done |
| UTS | `CLONE_NEWUTS` | Own hostname | done |
| Mount | `CLONE_NEWNS` | Own mount table; allows `pivot_root` and private `/proc` | done |
| IPC | `CLONE_NEWIPC` | Own SysV IPC / POSIX message queues | planned |
| Network | `CLONE_NEWNET` | Own network stack (loopback only at first) | planned |

## Filesystem
The Alpine minirootfs in `rootfs/` becomes `/` via `pivot_root`; the old root is
unmounted. The rootfs is used in place, so changes persist (no overlay yet).

## Resource limits: cgroups v2 (planned)
Per-container directory under `/sys/fs/cgroup/minibox/<id>/` with
`memory.max`, `cpu.max`, `pids.max`; the child's PID is written to `cgroup.procs`.

## Planned components
1. **Runtime core (C)** – done for Phase 1.
2. **CLI** – `run`, `ps`, `stop`, `exec` (`setns`), `logs`.
3. **State store** – per-container metadata (pid, limits, log path).
4. **Web UI (Python Flask)** – lists and controls containers via the CLI.
5. **Benchmarks** – startup time, limit enforcement, comparison to a bare process.

## Phases
0. Setup · **1. Core isolation** · 2. IPC/network · 3. cgroups · 4. CLI · 5. Web UI · 6. Benchmarks
