# src/exec.c

## Purpose
`minibox exec <id|name> -- COMMAND [ARGS...]`: run a command inside an **already running** container. The command
shares the container's IPC, UTS, network, PID and mount namespaces and its cgroup, and uses the caller's stdin, stdout
and stderr (so `exec <id> -- sh` is an interactive shell). The exit status is the command's.

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
5. **Parent**: `setns(pid)` (affects only children), ignore SIGINT/SIGQUIT, `fork`, `waitpid`.
6. **Child** (now in the container's PID namespace): join the cgroup (host path), `setns` ipc → uts → net → **mnt**,
   `fchdir(root)` + `chroot(".")` + `chdir("/")`, build the environment, `execvp`.

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
| static `child_main(st, t, term, argv)` | – | never returns | setup failure → `_exit(1)`; closes the descriptors; `execvp`; failure → `exec X: strerror`, `_exit(127)` |
| static `wait_for_child(pid)` | child PID | exit status / 128+N / 1 | `waitpid` retried on `EINTR` |
| static `ignore_interrupts(old_int, old_quit)` / `restore_interrupts(...)` | saved actions | 0 / -1 | `sigaction` for SIGINT and SIGQUIT |
| static `run_in_container(st, t, term, argv)` | – | status / -1 | `setns(pid)`, ignore signals, `fork`, wait |

Constants/tables: `NAMESPACES[]` = `{name, CLONE_NEW*}` for ipc, uts, net, pid, mnt (mnt last, see below); `DEFAULT_TERM` = `xterm`
when the caller has no `TERM`.

## System calls used and why
- `open` on `/proc/<pid>/ns/*` and `/proc/<pid>/root` – get descriptors for the container's namespaces and root directory.
- `setns(fd, CLONE_NEW*)` – move the calling process into an existing namespace.
- `fork`, `waitpid` – needed because a PID-namespace join only affects children; the parent also reports the status.
- `fchdir`, `chroot`, `chdir` – put the process in the container's root directory.
- `open`/`write` on `cgroup.procs` (through `cgroup_join_leaf`) – join the container's resource limits.
- `sigaction` – ignore SIGINT/SIGQUIT in the waiting parent.
- `execvp` – replace the child with the command (searches the container's `PATH`).

## OS concept
Namespaces are objects that processes can join (`setns`), not only create (`clone`/`unshare`). `exec` is how a
runtime lets you "get into" a container. The ordering matters:
- all descriptors are opened first, because after joining the mount namespace `/proc` is the container's and no longer shows the host's PIDs;
- the cgroup is written first because it needs the host's `/sys/fs/cgroup`;
- the mount namespace is joined last for both reasons.

Limitations: only a PID that is the init of the container is trusted (no check of the ns files after opening); SIGTERM sent
to `minibox exec` is not forwarded to the command; no user namespace or capability dropping (as in `run`).

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
sudo ./minibox exec $id -- sh          # interactive; 'exit' to leave
sudo ./minibox stop $id; sudo ./minibox rm $id
```
