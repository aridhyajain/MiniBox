# src/exec.c

## Purpose
`minibox exec <id|name> -- COMMAND [ARGS...]`: run a command inside an **already running** container. The command
shares the container's IPC, UTS, network, PID and mount namespaces and its cgroup, and uses the caller's stdin, stdout
and stderr (so `exec <id> -- sh` is an interactive shell). The exit status is the command's. The command **dies with
`minibox exec`**: if exec is stopped, the command does not keep running inside the container (see "Dying together").

## Usage
```
sudo ./minibox exec demo -- hostname
sudo ./minibox exec 3fa9c01be274 -- sh -c 'echo $$'
sudo ./minibox exec demo -- sh                   # interactive
```

## Flow
1. **Parse** – needs `<key> -- COMMAND`. No argument or a key starting with `-` → usage; no `--` or nothing after it → `no command given`.
2. **Root** – `require_root()` (from `commands.c`): `setns`, `chroot` and writing to the cgroup need root.
3. **Look up** – `state_find` (unknown → `no such container`), then `state_effective_status` must be `running`
   (`exited` → `not running (status: exited)`, `dead` → use `rm`). The stored PID must still be a PID-namespace init
   (`state_pid_is_ns_init`), which guards against a reused PID.
4. **Open** `/proc/<pid>/ns/{ipc,uts,net,pid,mnt}` and `/proc/<pid>/root` as descriptors, all before joining anything.
5. **Parent**: `setns(pid)` (affects only children), choose the mode (below), block SIGTERM/SIGINT/SIGHUP, `fork`, set up signals, `waitpid`.
6. **Child** (now in the container's PID namespace): `prctl(PR_SET_PDEATHSIG, SIGKILL)` and re-check the parent; in group mode `setpgid(0,0)`;
   join the cgroup (host path), `setns` ipc → uts → net → **mnt**, `fchdir(root)` + `chroot(".")` + `chdir("/")`, build the environment, `execvp`.

## Dying together (why and how)
**Problem.** `waitpid` in the parent does not protect the command: if `minibox exec` is killed (a timeout in the web UI, `kill`, a closed SSH session),
the command keeps running. Its parent is gone, so the kernel reparents it to the **init of its PID namespace** (the container's PID 1). It stays in
the container's cgroup and still counts against `pids.max` until it ends by itself.

**Fix 1: `PR_SET_PDEATHSIG` (always).** Right after `fork` the child calls `prctl(PR_SET_PDEATHSIG, SIGKILL)`: when its parent dies the kernel
sends it SIGKILL. The parent may have died *before* the call, and then no signal would ever arrive, so the child re-checks. Its parent is on the host,
outside the container's PID namespace, and a process cannot see a parent outside its namespace: `getppid()` is 0 while the parent lives and becomes
the container's init (non-zero) after reparenting. `getppid() != 0` therefore means "minibox exec is already gone": the child exits. The setting
survives `execve`, so it applies to the command itself.

**Fix 2: process group + signal forwarding (only when stdin is not a terminal).** When `isatty(0)` is false (scripts, the web UI) the child calls
`setpgid(0,0)` and becomes the leader of a new process group, and the parent also calls `setpgid(child, child)` so the group exists whichever side runs
first. Everything the command starts inherits the group. The parent installs handlers for SIGTERM, SIGINT and SIGHUP; the handler only does
async-signal-safe things (`kill(-pgid, SIGKILL)`, `kill(pid, SIGKILL)`, two plain stores). Then `waitpid` returns, the parent reaps the child and exits
with **128 + the signal** (143 for SIGTERM). The three signals are blocked across `fork` so one that arrives early waits until the handler and the PID are ready.

**Interactive terminals are different.** With `exec -- sh` on a terminal the command must stay in the terminal's foreground process group, otherwise
the shell would get job-control trouble (SIGTTIN/SIGTTOU when it reads the terminal) and Ctrl-C would no longer reach it. So with a tty the behaviour is
as before: same process group, SIGINT/SIGQUIT ignored in the parent while it waits, no forwarding handlers. Only `PR_SET_PDEATHSIG` still applies there
(for example when the terminal window is closed and the parent dies).

**Limits.** Nothing can run after SIGKILL, so SIGKILL of `minibox exec` cannot be forwarded: the direct child dies through PDEATHSIG, but a
**grandchild** (e.g. the second `sleep` in `sh -c 'sleep 30 & sleep 30'`) survives, because PDEATHSIG is not inherited by a process's own children.
A command that leaves its process group (`setsid`, `setpgid`) also escapes the group kill. Both survivors stay in the container's cgroup until they exit,
and are removed by `stop`/`rm` (killing the container's init kills the whole PID namespace). In a normal exit the parent does nothing about leftover
background processes (as before). `getppid() != 0` assumes `minibox exec` runs outside the container's PID namespace (always true for the host binary).

**With `--init`.** If the container was started with `run --init`, its PID 1 is `minibox-init` (`src/minit.c`), which reaps orphans with `waitpid(-1)`. A command left behind by a
killed `minibox exec` is then reparented to the init and cleaned up as soon as it dies: no zombie, and `pids.current` goes back to the baseline. `exec` itself needs no change:
`state_pid_is_ns_init` still sees PID 1 in the container's namespace, and `setns` + the cgroup join work the same.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int cmd_exec(int argc, char **argv)` | `argv[0]="exec"`, key, `--`, command | command's exit status (128+N if signalled, 127 if not executable), 1 on a minibox error | usage / `no command given` / not root / unknown, not running or dead container / cannot open the ns files / `fork` failed |
| static `usage_exec()` | – | – | usage text on stderr |
| static `find_running(key, st)` | key, state out | 0 / -1 | message for unknown, dead, not running, or init gone |
| static `open_target(pid, t)` / `close_target(t)` | init PID, descriptor set | 0 / -1 | `open` of the five ns files and `root`; `ENOENT` → "the container has just exited"; closes everything on failure |
| static `open_path(path)` | path | fd / -1 | `open(O_RDONLY\|O_CLOEXEC)` with a clear message |
| static `join_namespace(t, i)` | descriptor set, table index | 0 / -1 | `setns(fd, CLONE_NEW*)`; the type flag makes the kernel verify the fd really is that kind of namespace |
| static `join_cgroup(id)` | container ID | 0 / -1 | `cgroup_join_leaf(id, getpid())`; no leaf (container has no limits) is fine |
| static `enter_root(root_fd)` | fd of `/proc/<pid>/root` | 0 / -1 | `fchdir`, `chroot(".")`, `chdir("/")` |
| static `set_environment(term)` | caller's TERM | 0 / -1 | `clearenv`, then `PATH=DEFAULT_PATH` (same as `run`), `HOME=/root`, `TERM` |
| static `child_setup(st, t, term)` | – | 0 / -1 | cgroup → namespaces (mnt last; pid skipped, the parent did it) → root → environment |
| static `die_with_parent()` | – | 0 / -1 | `prctl(PR_SET_PDEATHSIG, SIGKILL)`, then fails if `getppid() != 0` (parent already gone) |
| static `child_main(st, t, term, argv, group_mode)` | – | never returns | `die_with_parent`; in group mode `setpgid(0,0)`; setup failure → `_exit(1)`; closes the descriptors; `execvp`; failure → `exec X: strerror`, `_exit(127)` |
| static `wait_for_child(pid)` | child PID | exit status / 128+N / 1; **128+signal if a forwarded signal arrived** | `waitpid` retried on `EINTR` |
| static `ignore_interrupts(old_int, old_quit)` / `restore_interrupts(...)` | saved actions | 0 / -1 | `sigaction` for SIGINT and SIGQUIT (tty mode only) |
| static `kill_command(sig)` | signal number | – | signal handler: stores the signal, `kill(-pgid, SIGKILL)` and `kill(pid, SIGKILL)`; async-signal-safe only; preserves `errno` |
| static `install_kill_handlers(old)` / `restore_kill_handlers(old)` | saved actions | 0 / -1 | `sigaction` for SIGTERM, SIGINT, SIGHUP (group mode only) |
| static `run_in_container(st, t, term, argv)` | – | status / -1 | `setns(pid)`, block the three signals, pick the mode with `isatty(0)`, `fork`, `setpgid(child, child)`, handlers, unblock, wait |

Global state (needed because a signal handler gets no arguments): `g_child_pgid` (`volatile pid_t`) and `g_signal` (`volatile sig_atomic_t`).

Constants/tables: `NAMESPACES[]` = `{name, CLONE_NEW*}` for ipc, uts, net, pid, mnt (mnt last, see below); `DEFAULT_TERM` = `xterm`
when the caller has no `TERM`.

## System calls used and why
- `open` on `/proc/<pid>/ns/*` and `/proc/<pid>/root` – get descriptors for the container's namespaces and root directory.
- `setns(fd, CLONE_NEW*)` – move the calling process into an existing namespace.
- `fork`, `waitpid` – needed because a PID-namespace join only affects children; the parent also reports the status.
- `fchdir`, `chroot`, `chdir` – put the process in the container's root directory.
- `open`/`write` on `cgroup.procs` (through `cgroup_join_leaf`) – join the container's resource limits. This is a cgroup *migration*: **`exec` still pays the migration cost** that `run` no longer pays (`run` creates the container inside its cgroup with `clone3`). `exec` could use the same technique (open the leaf before `setns`, replace `fork()` with `clone3(CLONE_INTO_CGROUP)`), but it has not been changed.
- `sigaction`, `sigprocmask` – tty mode: ignore SIGINT/SIGQUIT in the waiting parent; group mode: handlers for SIGTERM/SIGINT/SIGHUP, blocked across `fork`.
- `prctl(PR_SET_PDEATHSIG, SIGKILL)` – the kernel kills the command if `minibox exec` dies.
- `getppid` – detect that the parent died before `prctl` ran (0 = parent alive and outside the PID namespace).
- `isatty`, `setpgid`, `kill(-pgid, SIGKILL)` – non-interactive mode: own process group and one signal for the whole group.
- `execvp` – replace the child with the command (searches the container's `PATH`).

## OS concept
Namespaces are objects that processes can join (`setns`), not only create (`clone`/`unshare`). `exec` is how a
runtime lets you "get into" a container. The ordering matters:
- all descriptors are opened first, because after joining the mount namespace `/proc` is the container's and no longer shows the host's PIDs;
- the cgroup is written first because it needs the host's `/sys/fs/cgroup`;
- the mount namespace is joined last for both reasons.

Also: orphan reparenting inside a PID namespace (to its init), parent-death signals, process groups and signal delivery to a group.

Limitations: only a PID that is the init of the container is trusted (no check of the ns files after opening); SIGKILL of `minibox exec`
cannot be forwarded and a grandchild can survive it (see "Dying together"); no user namespace or capability dropping (as in `run`).

## How to test
```sh
make
./minibox exec                         # usage
./minibox exec abc                     # "no command given"
sudo tests/phase4b_test.sh             # full check
# by hand:
id=$(sudo ./minibox run -d --hostname boxy --memory 64M -- sleep 300)
sudo ./minibox exec $id -- hostname    # boxy
sudo ./minibox exec $id -- ps          # PID 1 is sleep
sudo ./minibox exec $id -- sh          # interactive; 'exit' to leave (tty mode, unchanged)
# the command dies with exec (stdin not a tty):
sudo ./minibox exec $id -- sleep 30 </dev/null & pid=$!; sleep 1
sudo kill -TERM $pid; sudo ./minibox exec $id -- ps    # no 'sleep 30' left; the background job exited with 143
sudo ./minibox stop $id; sudo ./minibox rm $id
```
