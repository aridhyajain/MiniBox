# minibox architecture

## Goal
Run a command inside an isolated environment (a "container") built only from
Linux kernel primitives, without Docker.

## Status
- **Phase 1 (implemented):** PID, UTS and mount namespaces; `pivot_root` into the Alpine rootfs.
- **Phase 2 (implemented):** IPC and network namespaces; loopback; optional veth link (`--net veth`). No user namespace.
- **Phase 3 (implemented):** cgroups v2 limits (`--memory`, `--cpus`, `--pids`), `--stats`, cleanup on every exit path.
- Later: CLI (ps/stop/exec/logs), Flask UI, benchmarks.

## Modules
```
src/main.c        parse + validate args (incl. limits), check root, validate rootfs
    |  container_run(cfg)
    v
src/container.c   mmap stack, pipes, clone(NEWPID|NEWUTS|NEWNS|NEWIPC|NEWNET), waitpid
    |  (in child)  rootfs_setup(rootfs), net_loopback_up(), net_set_ipv4()
    |  (in parent) cgroup_setup(pid), net_veth_setup(pid), cleanup of both
    v
src/rootfs.c      private mounts, bind, /dev, pivot_root, /proc, /tmp
src/net.c         loopback + address via ioctl (child); veth via `ip` (parent)
src/cgroup.c      cgroup v2 check, leaf create, limits, stats, rmdir (file I/O only)
```
Headers: `include/container.h`, `include/rootfs.h`, `include/net.h`, `include/cgroup.h`.

## Setup order
Child (inside the new namespaces; PID 1):
1. close the parent's pipe ends; `sethostname(hostname)` – UTS ns
2. bring up `lo` (ioctl) – network ns
3. `rootfs_setup`: private mounts, bind rootfs, `/dev`, `pivot_root`, `/proc`, `/tmp`
4. write `'R'` to the *ready* pipe, then **block** reading the *go* pipe
5. on `'G'`: (veth) set `eth0` to 10.0.0.2/24 and up; `chdir("/")`, set `PATH`/`HOME`, `execvp(command)`.
   On `'A'` or EOF: exit 1.

Parent: (limits) check cgroup v2 before cloning; install SIGINT/SIGTERM handlers; `clone`; wait for `'R'`;
(limits) create `box-<pid>`, write limits, write the PID to `cgroup.procs`; (veth) create the pair, move the peer into the
child's netns, configure the host end; write `'G'` (or `'A'` + SIGKILL if anything failed); `waitpid`; delete the host veth
if it still exists; (limits) `--stats`, OOM check (exit 137), `rmdir` the leaf; restore handlers;
return the child's exit code (128+N for signal N). SIGINT/SIGTERM at any point kill the container and the same cleanup runs.

## Network topology (`--net veth`)
```
 host netns                                  container netns
 vethh<pid> 10.0.0.1/24  <==== veth ====>    eth0 (was vethc<pid>) 10.0.0.2/24
                                             lo 127.0.0.1
```
`--net none` (default): the container has only `lo`.
Interface names are unique per PID, but the subnet is fixed, so run one veth container at a time. No NAT/internet.

## Isolation: namespaces (`clone`/`unshare`)
| Namespace | Flag | Effect | Status |
|-----------|------|--------|--------|
| PID | `CLONE_NEWPID` | Own process tree; first process is PID 1 | done |
| UTS | `CLONE_NEWUTS` | Own hostname | done |
| Mount | `CLONE_NEWNS` | Own mount table; allows `pivot_root` and private `/proc` | done |
| IPC | `CLONE_NEWIPC` | Own SysV message queues, shared memory, semaphores | done |
| Network | `CLONE_NEWNET` | Own interfaces, routes, ports (loopback, optional veth) | done |

## Filesystem
The Alpine minirootfs in `rootfs/` becomes `/` via `pivot_root`; the old root is
unmounted. The rootfs is used in place, so changes persist (no overlay yet).

## Resource limits: cgroups v2
```
/sys/fs/cgroup                 root: subtree_control +memory +cpu +pids
└── minibox/                   created once and kept; never holds processes; enables controllers for leaves
    └── box-<host pid>/        one per container: memory.max, memory.swap.max=0, memory.oom.group=1,
                               cpu.max "<quota> 100000", pids.max, cgroup.procs
```
Only created when `--memory`, `--cpus` or `--pids` is given. The PID is moved in before the child is released,
so limits apply before `exec`. After exit: optional stats (`memory.peak`, `cpu.stat usage_usec`, `pids.peak`,
`memory.events oom_kill`), OOM → exit 137, then the leaf is removed (`rmdir`, retried up to 1 s).

## Planned components
1. **Runtime core (C)** – namespaces, rootfs, network and cgroups done (Phases 1–3).
2. **CLI** – `run`, `ps`, `stop`, `exec` (`setns`), `logs`.
3. **State store** – per-container metadata (pid, limits, log path).
4. **Web UI (Python Flask)** – lists and controls containers via the CLI.
5. **Benchmarks** – startup time, limit enforcement, comparison to a bare process.

## Phases
0. Setup · 1. Core isolation · 2. IPC/network · **3. cgroups** · 4. CLI · 5. Web UI · 6. Benchmarks
