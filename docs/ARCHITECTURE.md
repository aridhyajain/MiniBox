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
- **Phase 5 (implemented):** local web UI (Flask) that launches and manages containers by calling the CLI; token, Host check and loopback-only binding.
- **Phase 6 (implemented):** benchmarks, charts, limit-enforcement checks.
- **clone3 into the cgroup (implemented):** with limits, `box-<id>` and its limits are created *before* the container, which is then created inside it with `clone3(CLONE_INTO_CGROUP)`; `clone()` + `cgroup.procs` remains as fallback (`MINIBOX_NO_CLONE3=1` forces it).
- **`--init` (implemented):** opt-in tiny PID 1 (`src/minit.c`) that forwards signals and reaps orphans.

## Modules
```
src/main.c        dispatch: run | ps | logs | stop | rm | exec
    |
src/commands.c    cmd_run, cmd_ps, cmd_logs, cmd_stop, cmd_rm (own usage + argument handling)
    |  options.c: parse/validate `run` options    state.c: containers on disk, IDs, subnets, liveness
    |  monitor.c: run a container and record how it ended (foreground, or detached via fork+setsid)
    |  container_run(cfg)
    v
src/container.c   pipes, cgroup_create(id, limits), clone3(NEWPID|NEWUTS|NEWNS|NEWIPC|NEWNET|CLONE_INTO_CGROUP)
    |             (fallback: mmap stack + clone() + cgroup_add_process), waitpid, on_started hook
    |  (in child)  rootfs_setup(rootfs), net_loopback_up(), net_set_ipv4()
    |  (in parent) net_veth_setup(pid, n), cleanup of veth and cgroup
    v
src/minit.c       `--init` only: runs as PID 1 inside the container, forks the command, forwards signals, reaps all children
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
    │                     command rootfs hostname net memory cpus pids init ip   (written via temp file + rename)
    └── log               stdout+stderr of a detached container (O_APPEND)
```
`status` is `running` or `exited`; `dead` is never stored: it is shown when the status is `running` but
`kill(monitor_pid, 0)` fails. `pid` is the container's init as seen from the host (with `--init`: `minibox-init`).
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
5. on `'G'`: (veth) set `eth0` to 10.0.n.2/24 and up; `chdir("/")`, set `PATH`/`HOME`, `execvp(command)`
   (with `--init`: `minit_run(command)` instead, see below). On `'A'` or EOF: exit 1.

Parent: (limits) check cgroup v2; make the sync pipes; install SIGINT/SIGTERM handlers; (limits) **create `box-<id>` and write its limits before any
process exists**; create the child with `clone3(..., CLONE_INTO_CGROUP)` so it is born inside the cgroup (no migration; on `ENOSYS`/`EPERM`/...
or with `MINIBOX_NO_CLONE3=1`: `clone()` and, after `'R'`, write the PID to `cgroup.procs`); wait for `'R'`; (veth) create the pair, move the peer into the
child's netns, configure the host end; write `'G'` (or `'A'` + SIGKILL if anything failed); call `on_started(pid)`; `waitpid`; delete the host veth
if it still exists; (limits) `--stats`, OOM check (exit 137), `rmdir` the leaf; restore handlers;
return the child's exit code (128+N for signal N). SIGINT/SIGTERM at any point kill the container and the same cleanup runs.

## `--init`: a tiny PID 1 (opt-in)
```
without --init                         with --init
PID 1: the command                     PID 1: minibox-init  (src/minit.c)
                                         |-- PID 2: the command
                                         '-- orphans of any depth are reparented here and reaped
```
`child_main` calls `minit_run(argv)` instead of `execvp`. The init blocks SIGCHLD/SIGTERM/SIGINT/SIGHUP/SIGQUIT/SIGUSR1/SIGUSR2, forks the command (which restores the
mask and default handlers and execs; exit 127/126 if it cannot), then loops: reap everything with `waitpid(-1, WNOHANG)`, wait for a signal with `sigtimedwait`, forward it
to the command (except tty-generated ones, which the command already got from the terminal). When the command is gone, the init exits with its code (128+N if signalled);
the monitor and `stop` see that code, so `stop` gives 143. The state file stores `init=1`, `ps` shows it, and `exec` works unchanged because the init is still the
PID-namespace init (`NSpid` ends in 1). Details: `docs/src/minit.md`.

## exec: entering a running container (Phase 4b)
`minibox exec <id|name> -- cmd...` does not create a container; it moves a new process into what `run` built:
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

**The command dies with `minibox exec`.** Right after step 4 the child calls `prctl(PR_SET_PDEATHSIG, SIGKILL)` (and checks `getppid()` to close the race), so
it is killed if `minibox exec` dies; without it an orphan would be reparented to the container's PID 1 and keep counting against `pids.max`. When stdin is
not a terminal (scripts, the web UI) the child also becomes a process-group leader and SIGTERM/SIGINT/SIGHUP sent to `minibox exec` are turned into
`kill(-pgid, SIGKILL)` (exit status 128+signal), which reaches grandchildren. With a terminal on stdin nothing changes except PDEATHSIG, so interactive
`exec -- sh` keeps normal job control. SIGKILL of `minibox exec` cannot be forwarded: a grandchild may survive it. See `docs/src/exec.md`.

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
Only created when `--memory`, `--cpus` or `--pids` is given. The cgroup and its limits are created first and the container is created
directly inside it (`clone3` + `CLONE_INTO_CGROUP`), so limits apply from the child's first instruction and no cgroup migration happens.
Why: writing a PID to `cgroup.procs` was the slow step in strace; a later probe showed the slowness is not a constant cost of limits but is observed after a veth teardown (mechanism: unverified hypothesis, an RCU grace period; `clone3` avoided it in the measurements). See `docs/BENCHMARKS.md`. Fallback: `clone()` and a PID write before the child is released;
`MINIBOX_NO_CLONE3=1` forces it (diagnostic switch for benchmarks/tests). `minibox exec` still joins by writing `cgroup.procs` and so still pays the migration. After exit: optional stats (`memory.peak`, `cpu.stat usage_usec`, `pids.peak`,
`memory.events oom_kill`), OOM → exit 137, then the leaf is removed (`rmdir`, retried up to 1 s).

## Web UI (Phase 5)
```
browser ──HTTP, 127.0.0.1 only, Bearer token──> ui/app.py (Flask, runs as root)
                                                  │ validation.py  (whitelist every field)
                                                  ▼
                                          ui/minibox_client.py ──argv list, no shell, timeout──> ./minibox ──> state dir, cgroups, namespaces
```
- **The UI drives the CLI** instead of reimplementing it: `ps -a --json` is the machine-readable listing, `run -d`, `logs`, `stop`, `rm`
  and `exec` do the work. The runtime stays the single source of truth and the UI is a thin, replaceable layer.
- **Files:** `ui/app.py` (routes + security checks), `ui/minibox_client.py` (the only code that runs the binary), `ui/validation.py` (pure
  validation), `ui/templates/index.html`, `ui/static/app.js`, `ui/static/style.css`; `scripts/setup_ui.sh` (venv + pinned Flask),
  `scripts/run_ui.sh` (root, starts the server).
- **API (all under `/api`, JSON, token required):** `GET /containers`, `POST /containers`, `GET /containers/<id>/logs?tail=N`,
  `POST /containers/<id>/stop`, `DELETE /containers/<id>`, `POST /containers/<id>/exec`. Errors are always
  `{"error": {"code", "message", "field"?}}` with 400/401/404/409/413/500/503.
- **Security model:** loopback bind (non-loopback host refused), random token per start (`hmac.compare_digest`), `Host` header allow-list
  (DNS rebinding), no CORS, CSP `default-src 'self'`, strict validation, body cap 16 KB, at most 10 running containers, rootfs chosen by the
  server. **Limit:** a container here is not a hard security boundary (shared kernel, root, no user namespace/seccomp), so whoever can
  reach the UI effectively has root; it must stay on localhost. Details: `docs/ui/app.md`.
- **Not in scope:** live graphs or monitoring; the UI only manages containers.

## Planned components
1. **Runtime core (C)** – namespaces, rootfs, network and cgroups done (Phases 1–3).
2. **CLI + state store** – `run -d`, `ps`, `logs`, `stop`, `rm` done (Phase 4a); `exec` (`setns`) done (Phase 4b).
3. **Web UI (Python Flask)** – done (Phase 5), see "Web UI" below.
4. **Benchmarks** – done (Phase 6), see "Benchmarks (Phase 6)" below.

## Phases
0. Setup · 1. Core isolation · 2. IPC/network · 3. cgroups · 4a. container management · 4b. exec · **5. Web UI** · 6. Benchmarks
