# src/container.c

## Purpose
Creates the container process with `clone()` in new PID, UTS, mount, IPC and network namespaces,
places it in its own cgroup when limits are given, synchronises with the child through two pipes
(so all host-side setup finishes before the program starts), waits for it, and cleans up
(veth, cgroup) on every path — including setup failures and SIGINT/SIGTERM.

## Parent/child handshake (identical in every mode)
```
parent                                      child (PID 1 in new namespaces)
cgroup_check_v2()  (only if limits given; before clone)
install SIGINT/SIGTERM handlers
clone() --------------------------------->  sethostname, bring lo up, rootfs_setup
  wait on ready pipe  <-------------------  write 'R'
  cgroup_setup(id, pid): box-<id> + limits +        read go pipe  (blocks)
    write pid to cgroup.procs
  (--net veth) net_veth_setup(pid, n)
  write 'G' (or 'A' + SIGKILL on failure) ->  'G': (veth) eth0 10.0.<n>.2/24, chdir, execvp
on_started(pid) hook (monitor records pid)   'A' or EOF: exit 1
waitpid
net_veth_cleanup(pid)  (veth mode)
--stats: print usage; oom_kill > 0 -> message, exit 137
cgroup_remove()  (rmdir leaf, retry up to 1 s)
restore signal handlers
```
- The pid is written to `cgroup.procs` **before** `'G'`, so the limits apply from the program's first instruction.
  The child forks nothing before exec (the `ip` commands run in the parent), so moving that one PID is enough.
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
| `alloc_stack(void **top)` | out-pointer | mapping base or NULL | `mmap` 1 MiB (`MAP_STACK`); `perror` |
| `make_sync_pipes`, `close_pipes`, `close_fd` | pipe struct / fd | 0 / -1 | `pipe2(O_CLOEXEC)` x2 |
| `read_byte(fd, &c)` | – | 1 / 0 (EOF) / -1 | retries `EINTR` unless `stop_signal` is set |
| `write_byte(fd, c)` | – | 0 / -1 | retries `EINTR`; `perror` |
| `on_stop_signal(sig)` | signal | – | sets `stop_signal`; `kill(target_pid, SIGKILL)`; preserves `errno` |
| `install_stop_handlers(old)`, `restore_handlers(old)` | saved actions | 0 / -1, – | `sigaction` for SIGINT and SIGTERM |
| `set_hostname(name)` | hostname | 0 / -1 | `sethostname` in the UTS ns |
| `child_handshake(p)` | pipes | 0 / -1 | writes `'R'`, blocks for `'G'`; "aborted by parent" on `'A'`/EOF |
| `set_container_address(index)` | subnet index | 0 / -1 | `net_subnet_addrs` → `net_set_ipv4(eth0, 10.0.<index>.2, 24)` |
| `child_setup(args)` | `struct child_args` | 0 / -1 | close parent's pipe ends → hostname → `net_loopback_up` → `rootfs_setup` → handshake → (veth) `set_container_address` → `chdir("/")` → PATH/HOME (`DEFAULT_PATH`, now defined in `container.h` and shared with `exec`) |
| `child_main(arg)` | args | exit code | 1 on setup failure, 127 if `execvp` fails |
| `parent_wait_ready(fd)` | fd | 0 / -1 | -1 on EOF (child died) or on a stop signal |
| `parent_network_setup(cfg, pid)` | – | 0 / -1 | veth only: `net_veth_setup(pid, cfg->net_index)` |
| `parent_setup(cfg, pid, p, cg, &child_died)` | – | 0 / -1 | wait `'R'` → `cgroup_setup(cg, cfg->id, ...)` (leaf `box-<id>`, if limits; prints "cgroup setup failed; container stopped") → network → fails if a stop signal arrived |
| `release_child(p, ok)` | – | 0 if `'G'` sent | writes `'G'` or `'A'` |
| `kill_child(pid)` | pid | – | `kill(SIGKILL)`, `ESRCH` ignored |
| `wait_for_child(pid)` | pid | exit code, 128+sig, or -1 | `waitpid`, retries `EINTR` |
| `report_early_exit(child_died, result)` | – | – | prints why the container ended early (died in setup / received SIGINT/SIGTERM) |
| `finish_cgroup(cfg, cg, result)` | – | final exit code | if leaf exists: `--stats` output, OOM check (message + 137), `cgroup_remove` |
| `supervise(cfg, pid, pipes)` | – | exit code or -1 | parent side after `clone`: setup, release, then (if set and setup succeeded) `cfg->on_started(pid, cfg->on_started_arg)`, wait, veth + cgroup cleanup |
| `int container_run(const struct container_config *cfg)` | config | command's exit status (137 if OOM-killed), -1 if creation/cgroup/network setup failed | cgroup check, pipes, stack, handlers, `clone`, `supervise`, restore, `munmap` |

## Phase 4 additions
`container_config` gained `id` (names the cgroup leaf `box-<id>`), `net_index` (subnet `10.0.<n>.0/24`) and the
`on_started` hook. The hook runs in the supervising process right after the child was released, with the init PID as
seen from the host. The monitor (`src/monitor.c`) uses it to record `pid`/`started_at` and to tell `run -d` that the
container is up. Everything else is unchanged; the id and the subnet are chosen by `state_create` before `container_run`.

## System calls used and why
- `mmap`/`munmap` – child stack for `clone`.
- `clone(CLONE_NEWPID|CLONE_NEWUTS|CLONE_NEWNS|CLONE_NEWIPC|CLONE_NEWNET|SIGCHLD)` – fork with five new namespaces.
- `pipe2(O_CLOEXEC)`, `read`, `write`, `close` – the handshake.
- `sigaction`, `kill` – stop the container on SIGINT/SIGTERM; kill it if setup fails.
- `sethostname`, `chdir`, `setenv`, `execvp`, `waitpid` – as in earlier phases.
- cgroup file I/O is in `src/cgroup.c`.

## OS concept
Namespaces (what a process sees), cgroups (what it can use), process synchronisation with pipes, and
signal handling (async-signal-safe handlers, `EINTR`, PID-namespace init semantics).

## How to test
```sh
sudo tests/phase1_test.sh && sudo tests/phase2_test.sh && sudo tests/phase3_test.sh
sudo ./minibox run --rootfs rootfs --pids 32 -- sleep 100   # then Ctrl-C: container killed, cgroup removed
ls /sys/fs/cgroup/minibox                                     # no box-* left
```
