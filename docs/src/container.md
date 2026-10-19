# src/container.c

## Purpose
Creates the container process in new PID, UTS, mount, IPC and network namespaces, **directly inside its own cgroup** when limits are given
(`clone3()` with `CLONE_INTO_CGROUP`; fallback: `clone()` and a write to `cgroup.procs`), synchronises with the child through two pipes
(so all host-side setup finishes before the program starts), waits for it, and cleans up
(veth, cgroup) on every path — including setup failures and SIGINT/SIGTERM.

## Start-up order and handshake
```
parent                                      child (PID 1 in new namespaces)
cgroup_check_v2()  (only if limits given)
make sync pipes, install SIGINT/SIGTERM handlers
cgroup_create(id, limits): mkdir box-<id>, write limits   (limits only; no process yet)
open box-<id>/ ; clone3(NEW* | CLONE_INTO_CGROUP, cgroup=fd) -> sethostname, bring lo up, rootfs_setup
  close fd (parent)                           close fd (child, first thing)
  wait on ready pipe  <-------------------  write 'R'
  [fallback path only] write pid to cgroup.procs      read go pipe  (blocks)
  (--net veth) net_veth_setup(pid, n)
  write 'G' (or 'A' + SIGKILL on failure) ->  'G': (veth) eth0 10.0.<n>.2/24, chdir, execvp
                                                (--init: minit_run instead of execvp)
on_started(pid) hook (monitor records pid)   'A' or EOF: exit 1
waitpid
net_veth_cleanup(pid)  (veth mode)
--stats: print usage; oom_kill > 0 -> message, exit 137
cgroup_remove()  (rmdir leaf, retry up to 1 s)
restore signal handlers
```
- **Default (clone3) path:** the child is *born* in `box-<id>`, so its limits apply from its first instruction and no
  migration is needed. The process is a fork-style copy: `clone3` has no function or stack argument, the child continues after
  the call on a copy of the parent's stack, closes the cgroup fd, and `_exit(child_main(args))`.
- **Fallback path** (`clone()` with a `mmap`'d stack): the parent writes the pid to `cgroup.procs` **before** `'G'`, so the limits still apply before
  the program starts. This is the old behaviour and costs a cgroup migration (about 9-36 ms measured; see `docs/BENCHMARKS.md`).
- **When the fallback is used:** `MINIBOX_NO_CLONE3=1` is set (a *diagnostic switch* for benchmarks and tests; silent), or `clone3` fails with
  anything except `EAGAIN`/`ENOMEM` (e.g. `ENOSYS` old kernel, `EINVAL`/`E2BIG` unknown flag/size, `EPERM` seccomp filter). Then one line is printed:
  `minibox: clone3 unavailable (<reason>); using clone() + cgroup.procs`. `EAGAIN`/`ENOMEM` are real resource limits and are reported as errors.
- Without limits there is no cgroup and the plain `clone()` path is used, exactly as before.
- `exec` is not changed: `minibox exec` still joins the cgroup by writing `cgroup.procs`, so it still pays the migration cost.
- EOF on the `ready` pipe means the child died early: the parent reaps it and prints
  "container died during setup (exit status N)". Pipes are `O_CLOEXEC`. The parent keeps the `go` read end open
  until after its write, so that write can never raise `SIGPIPE`.

## Signals (the only global state)
`static volatile sig_atomic_t stop_signal, target_pid` — a signal handler receives no arguments, so globals are
the only way it can communicate. The handler (installed with `sigaction`, **no** `SA_RESTART`) records the signal and,
once `target_pid` is set right after `clone`, calls `kill(target_pid, SIGKILL)` (async-signal-safe). Killing PID 1
of a PID namespace kills every process in it. Blocking `read`/`waitpid` then return `EINTR`; `read_byte` stops
retrying, setup is treated as failed, and normal cleanup runs. `target_pid` is cleared right after `waitpid` reaps the
child so a later signal can never hit a recycled PID. Handlers are restored at the end; `exec` resets them in the child.
A signal arriving before `target_pid` is set is caught by the `stop_signal` check at the end of `parent_setup`.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `alloc_stack(void **top)` | out-pointer | mapping base or NULL | `mmap` 1 MiB (`MAP_STACK`); `perror`; used only by the `clone()` path |
| `make_sync_pipes`, `close_pipes`, `close_fd` | pipe struct / fd | 0 / -1 | `pipe2(O_CLOEXEC)` x2 |
| `read_byte(fd, &c)` | – | 1 / 0 (EOF) / -1 | retries `EINTR` unless `stop_signal` is set |
| `write_byte(fd, c)` | – | 0 / -1 | retries `EINTR`; `perror` |
| `on_stop_signal(sig)` | signal | – | sets `stop_signal`; `kill(target_pid, SIGKILL)`; preserves `errno` |
| `install_stop_handlers(old)`, `restore_handlers(old)` | saved actions | 0 / -1, – | `sigaction` for SIGINT and SIGTERM |
| `set_hostname(name)` | hostname | 0 / -1 | `sethostname` in the UTS ns |
| `child_handshake(p)` | pipes | 0 / -1 | writes `'R'`, blocks for `'G'`; "aborted by parent" on `'A'`/EOF |
| `set_container_address(index)` | subnet index | 0 / -1 | `net_subnet_addrs` → `net_set_ipv4(eth0, 10.0.<index>.2, 24)` |
| `child_setup(args)` | `struct child_args` | 0 / -1 | close parent's pipe ends → hostname → `net_loopback_up` → `rootfs_setup` → handshake → (veth) `set_container_address` → `chdir("/")` → PATH/HOME (`DEFAULT_PATH`, now defined in `container.h` and shared with `exec`) |
| `child_main(arg)` | args | exit code | 1 on setup failure; with `cfg->init` it returns `minit_run(argv)` (the tiny init, `src/minit.c`, stays PID 1 and runs the command as its child); otherwise 127 if `execvp` fails |
| `parent_wait_ready(fd)` | fd | 0 / -1 | -1 on EOF (child died) or on a stop signal |
| `parent_network_setup(cfg, pid)` | – | 0 / -1 | veth only: `net_veth_setup(pid, cfg->net_index)` |
| `parent_setup(cfg, pid, p, cg, in_cgroup, &child_died)` | – | 0 / -1 | wait `'R'` → if the leaf exists and the child was **not** created inside it: `cgroup_add_process` (prints "cgroup setup failed; container stopped" on error) → network → fails if a stop signal arrived |
| `release_child(p, ok)` | – | 0 if `'G'` sent | writes `'G'` or `'A'` |
| `kill_child(pid)` | pid | – | `kill(SIGKILL)`, `ESRCH` ignored |
| `wait_for_child(pid)` | pid | exit code, 128+sig, or -1 | `waitpid`, retries `EINTR` |
| `report_early_exit(child_died, result)` | – | – | prints why the container ended early (died in setup / received SIGINT/SIGTERM) |
| `finish_cgroup(cfg, cg, result)` | – | final exit code | if leaf exists: `--stats` output, OOM check (message + 137), `cgroup_remove` |
| `supervise(cfg, pid, pipes, cg, in_cgroup)` | – | exit code or -1 | parent side once the child exists: setup, release, then (if set and setup succeeded) `cfg->on_started(pid, cfg->on_started_arg)`, wait, veth + cgroup cleanup |
| `clone3_disabled()` | – | bool | `getenv("MINIBOX_NO_CLONE3")` equals `"1"` |
| `clone3_should_fall_back(err)` | errno | bool | true for every errno except `EAGAIN` and `ENOMEM` |
| `spawn_clone3(args, cgroup_fd)` | child args, leaf fd | pid, -1 (errno set); never returns in the child | fills `struct mb_clone_args` (88 bytes, our own copy of the kernel's `clone_args`): `flags = CLONE_NEW{PID,UTS,NS,IPC,NET} \| CLONE_INTO_CGROUP`, `exit_signal = SIGCHLD`, `cgroup = fd`; `syscall(SYS_clone3, ...)`; in the child: `close(fd)`, `_exit(child_main(args))` |
| `spawn_into_cgroup(args, cg)` | – | pid, `SPAWN_FALLBACK` (-2), or -1 | `cgroup_open_dir`, `spawn_clone3`, always closes the fd; on a fall-back-worthy errno prints the note and returns `SPAWN_FALLBACK`; otherwise `perror("clone3")` |
| `spawn_clone(args, &stack_base)` | – | pid / -1 | the old path: `alloc_stack` + `clone(child_main, top, NS_FLAGS \| SIGCHLD, args)` |
| `start_child(args, cg, &in_cgroup, &stack_base)` | – | pid / -1 | picks the path: clone3 if the leaf exists and it is not disabled, else `clone()`; `*in_cgroup` tells `parent_setup` whether the move is still needed |
| `launch(cfg, args, cg)` | – | exit code or -1 | `cgroup_create` (before any process; on failure removes the directory and returns -1), refuse to start if a stop signal arrived, `start_child`, `supervise`, `munmap` the stack if one was used; removes the leaf if no child could be created |
| `int container_run(const struct container_config *cfg)` | config | command's exit status (137 if OOM-killed), -1 if creation/cgroup/network setup failed | cgroup check, pipes, handlers, `launch`, restore |

## Phase 4 additions
`container_config` gained `id` (names the cgroup leaf `box-<id>`), `net_index` (subnet `10.0.<n>.0/24`) and the
`on_started` hook. The hook runs in the supervising process right after the child was released, with the init PID as
seen from the host. The monitor (`src/monitor.c`) uses it to record `pid`/`started_at` and to tell `run -d` that the
container is up. Everything else is unchanged; the id and the subnet are chosen by `state_create` before `container_run`.

## Phase 7 addition: `--init`
`container_config` gained `init`. When it is set, `child_main` does not `execvp` the command but calls `minit_run()` (`src/minit.c`): the cloned child stays
PID 1 as a tiny init, forks the command, forwards signals and reaps every child, and returns the command's exit status (128+N if signalled). The parent side is
unchanged: it waits for PID 1 and sees that status (so `stop` of a normal program gives 143 instead of 137). Without `--init` the code path is the same as before.

## System calls used and why
- `clone3(CLONE_NEWPID|CLONE_NEWUTS|CLONE_NEWNS|CLONE_NEWIPC|CLONE_NEWNET|CLONE_INTO_CGROUP)` (called through `syscall(SYS_clone3, ...)`, Linux 5.7+) – fork-like creation of the container directly inside its cgroup. Constants and the argument struct are defined locally if the headers lack them.
- `open(O_DIRECTORY)`/`close` – the cgroup directory descriptor.
- `mmap`/`munmap` – child stack for `clone` (fallback path only).
- `clone(CLONE_NEWPID|CLONE_NEWUTS|CLONE_NEWNS|CLONE_NEWIPC|CLONE_NEWNET|SIGCHLD)` – the fallback and the no-limits path.
- `pipe2(O_CLOEXEC)`, `read`, `write`, `close` – the handshake.
- `sigaction`, `kill` – stop the container on SIGINT/SIGTERM; kill it if setup fails.
- `sethostname`, `chdir`, `setenv`, `execvp`, `waitpid` – as in earlier phases.
- cgroup file I/O is in `src/cgroup.c`.

## OS concept
Namespaces (what a process sees), cgroups (what it can use) and why creating a process inside a cgroup is cheaper than migrating it, process synchronisation with pipes, and
signal handling (async-signal-safe handlers, `EINTR`, PID-namespace init semantics).

## How to test
```sh
sudo tests/phase1_test.sh && sudo tests/phase2_test.sh && sudo tests/phase3_test.sh
sudo bash tests/clone3_test.sh            # both paths, fallback via strace error injection, timing comparison
sudo MINIBOX_NO_CLONE3=1 ./minibox run --rootfs rootfs --memory 64M -- /bin/true   # force the old path
sudo ./minibox run --rootfs rootfs --pids 32 -- sleep 100   # then Ctrl-C: container killed, cgroup removed
ls /sys/fs/cgroup/minibox                                     # no box-* left
```
