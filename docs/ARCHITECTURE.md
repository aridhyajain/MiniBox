# minibox architecture

## Goal
Run a command inside an isolated environment (a "container") built only from
Linux kernel primitives, without Docker.

## Status
- **Phase 1 (implemented):** PID, UTS and mount namespaces; `pivot_root` into the Alpine rootfs.
- **Phase 2 (implemented):** IPC and network namespaces; loopback; optional veth link (`--net veth`). No user namespace.
- **Phase 3 (implemented):** cgroups v2 limits (`--memory`, `--cpus`, `--pids`), `--stats`, cleanup on every exit path.
- **Phase 4a (implemented):** container management: `run -d`, `ps`, `logs`, `stop`, `rm`; state on disk; a monitor process per container; one subnet per veth container.
- **Phase 4b (implemented):** `exec` (`setns` into a running container, joining its cgroup), `tests/run_all.sh`.
- Later: Flask UI, benchmarks.

## Modules
```
src/main.c        dispatch: run | ps | logs | stop | rm | exec
    |
src/commands.c    cmd_run, cmd_ps, cmd_logs, cmd_stop, cmd_rm (own usage + argument handling)
    |  options.c: parse/validate `run` options    state.c: containers on disk, IDs, subnets, liveness
    |  monitor.c: run a container and record how it ended (foreground, or detached via fork+setsid)
    |  container_run(cfg)
    v
src/container.c   mmap stack, pipes, clone(NEWPID|NEWUTS|NEWNS|NEWIPC|NEWNET), waitpid, on_started hook
    |  (in child)  rootfs_setup(rootfs), net_loopback_up(), net_set_ipv4()
    |  (in parent) cgroup_setup(id, pid), net_veth_setup(pid, n), cleanup of both
    v
src/rootfs.c      private mounts, bind, /dev, pivot_root, /proc, /tmp
src/net.c         loopback + address via ioctl (child); veth via `ip` (parent); subnet addresses
src/cgroup.c      cgroup v2 check, leaf create, limits, stats, rmdir, join (file I/O only)
src/exec.c        cmd_exec: open /proc/<pid>/ns/*, setns, fork, chroot, execvp (uses state.c and cgroup_join_leaf)
```
Headers: `include/{commands,options,state,monitor,container,rootfs,net,cgroup,exec}.h`.

## State directory (Phase 4a)
`$MINIBOX_STATE_DIR`, default `/var/lib/minibox/containers` (mode 0700):
```
containers/
├── .lock                 flock() while a container is being registered (name, ID, subnet are chosen atomically)
└── <id>/                 12 lowercase hex characters from getrandom(); created with mkdir (atomic claim)
    ├── state             key=value: id name status pid monitor_pid exit_code created_at started_at finished_at
    │                     command rootfs hostname net memory cpus pids ip   (written via temp file + rename)
    └── log               stdout+stderr of a detached container (O_APPEND)
```
`status` is `running` or `exited`; `dead` is never stored: it is shown when the status is `running` but
`kill(monitor_pid, 0)` fails. `pid` is the container's init as seen from the host.
Separate commands (`ps`, `stop`, ...) find containers only through these files and through signals.

## The monitor process (Phase 4a)
Every container has a monitor: the process that stays as its parent, waits for it and records the result.
Foreground `run`: the `minibox` process itself (state is deleted at the end, like `--rm`).
`run -d`:
```
minibox run -d  --fork-->  monitor: setsid(); stdin=/dev/null; stdout/stderr=<id>/log; chdir("/")
   waits on a pipe          container_run(): clone, cgroup box-<id>, veth, release child
   <---- 'S' ------------   on_started(): save pid + started_at, tell parent
   prints <id>, exits       waitpid ... remove cgroup + veth ... save status=exited, exit_code, finished_at
```
If the monitor dies, nothing records the exit or cleans up: `ps` shows `dead`, `rm` kills the leftover init and removes
`box-<id>` and the host veth. `stop` sends SIGTERM to the init, polls the state file, then SIGKILL (PID 1 ignores
SIGTERM without a handler).

## Setup order
Child (inside the new namespaces; PID 1):
1. close the parent's pipe ends; `sethostname(hostname)` – UTS ns
2. bring up `lo` (ioctl) – network ns
3. `rootfs_setup`: private mounts, bind rootfs, `/dev`, `pivot_root`, `/proc`, `/tmp`
4. write `'R'` to the *ready* pipe, then **block** reading the *go* pipe
5. on `'G'`: (veth) set `eth0` to 10.0.n.2/24 and up; `chdir("/")`, set `PATH`/`HOME`, `execvp(command)`.
   On `'A'` or EOF: exit 1.

Parent: (limits) check cgroup v2 before cloning; install SIGINT/SIGTERM handlers; `clone`; wait for `'R'`;
(limits) create `box-<id>`, write limits, write the PID to `cgroup.procs`; (veth) create the pair, move the peer into the
child's netns, configure the host end; write `'G'` (or `'A'` + SIGKILL if anything failed); call `on_started(pid)`; `waitpid`; delete the host veth
if it still exists; (limits) `--stats`, OOM check (exit 137), `rmdir` the leaf; restore handlers;
return the child's exit code (128+N for signal N). SIGINT/SIGTERM at any point kill the container and the same cleanup runs.

## exec: entering a running container (Phase 4b)
`minibox exec <id|name> -- cmd...` does not create anything; it moves a new process into what `run` built:
```
minibox exec (host)                                   container (init = host PID P)
 1. state_find, status must be running, pid = P
 2. open /proc/P/ns/{ipc,uts,net,pid,mnt}, /proc/P/root   <- all before joining anything
 3. setns(pid ns)            affects only children
 4. fork ------------------> child (inside the container's PID namespace)
                              5. write own PID to /sys/fs/cgroup/minibox/box-<id>/cgroup.procs   (host path!)
                              6. setns(ipc), setns(uts), setns(net), setns(mnt)   <- mnt last
                              7. fchdir(root fd), chroot("."), chdir("/")
                              8. env: PATH, HOME=/root, TERM (caller's); execvp(cmd)
 9. waitpid -> exit status (128+N if killed by signal N)
```
The waiting parent stays in the host namespaces and outside the cgroup. The command inherits the caller's
stdin/stdout/stderr. A container started without limits has no cgroup, so step 5 is skipped.

## Network topology (`--net veth`)
```
 host netns                                  container netns
 vethh<pid> 10.0.n.1/24  <==== veth ====>    eth0 (was vethc<pid>) 10.0.n.2/24
                                             lo 127.0.0.1
```
`--net none` (default): the container has only `lo`.
`n` is the lowest subnet number not used by another running container (a single container gets `n = 0`, i.e.
10.0.0.x); it is chosen under the state lock and stored as `ip` in the state. Interface names are unique per PID.
No NAT/internet, no bridge: containers cannot reach each other.

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
    └── box-<container id>/    one per container: memory.max, memory.swap.max=0, memory.oom.group=1,
                               cpu.max "<quota> 100000", pids.max, cgroup.procs
```
Only created when `--memory`, `--cpus` or `--pids` is given. The PID is moved in before the child is released,
so limits apply before `exec`. After exit: optional stats (`memory.peak`, `cpu.stat usage_usec`, `pids.peak`,
`memory.events oom_kill`), OOM → exit 137, then the leaf is removed (`rmdir`, retried up to 1 s).

## Planned components
1. **Runtime core (C)** – namespaces, rootfs, network and cgroups done (Phases 1–3).
2. **CLI + state store** – `run -d`, `ps`, `logs`, `stop`, `rm` done (Phase 4a); `exec` (`setns`) done (Phase 4b).
3. **Web UI (Python Flask)** – lists and controls containers via the CLI (`ps --json`).
4. **Benchmarks** – startup time, limit enforcement, comparison to a bare process.

## Phases
0. Setup · 1. Core isolation · 2. IPC/network · 3. cgroups · 4a. container management · **4b. exec** · 5. Web UI · 6. Benchmarks
