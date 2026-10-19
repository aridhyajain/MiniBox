# Viva notes

## Phase 1

### What is a namespace?
A namespace wraps a global kernel resource so that processes inside see their own private copy.
The kernel is the same; only the *view* differs. Each process belongs to one namespace of each
type (`/proc/<pid>/ns/`). A container is just a process placed in a set of new namespaces
(plus cgroup limits later).

### What each flag isolates
| Flag | Isolates | Visible effect |
|------|----------|----------------|
| `CLONE_NEWPID` | process IDs | the container has its own PID numbering; it can't see or signal host processes |
| `CLONE_NEWUTS` | hostname / domain name | `sethostname` inside doesn't change the host's name |
| `CLONE_NEWNS` | the mount table | mounts/unmounts inside don't appear on the host; allows swapping `/` |
| (later) `CLONE_NEWIPC`, `CLONE_NEWNET` | IPC objects, network stack | planned for later phases |

`SIGCHLD` is not a namespace flag; it just makes the child behave like a normal `fork` child so `waitpid` works.

### Why is the child PID 1?
`clone` with `CLONE_NEWPID` creates a new PID namespace, and the first process created in it
gets PID 1 there. (The parent still sees it with a normal, large PID.) PID 1 is special: it
adopts orphans and, if it dies, the kernel kills every other process in that namespace.

### Why pivot_root and not chroot?
`chroot` only changes where "/" points for a process; the old filesystem is still mounted and a
root process can often escape (e.g. via a leftover directory fd or `chroot` + `..`).
`pivot_root` changes the root of the whole *mount namespace*: the old root is moved to a
directory inside the new one, and we then `umount2(MNT_DETACH)` it. After that the host filesystem
is not reachable from inside at all.

### Why make mount propagation private?
On systemd (and WSL) the host's `/` is mounted *shared*. A new mount namespace starts as a copy
whose mounts are still linked to the host's by propagation, so mounts and unmounts we do
(bind, `/proc`, unmounting the old root) could propagate back and alter or break the host.
`mount("none","/",NULL,MS_REC|MS_PRIVATE,NULL)` cuts the link, so everything we do stays inside the container.

### Other things worth knowing
- `pivot_root` requires the new root to be a mount point, hence the bind mount of the rootfs onto itself.
- `/proc` is mounted *after* entering the PID namespace so `ps` shows only container processes.
- `/dev` is a small tmpfs with 5 nodes (`null`, `zero`, `random`, `urandom`, `tty`) rather than the host's `/dev`.
- The child stack is `mmap`ed because `clone` (unlike `fork`) doesn't copy the stack; it grows down so we pass the top.

## Phase 2

### What does the IPC namespace isolate?
The System V IPC objects: **message queues, shared memory segments and semaphore sets** (plus POSIX
message queues). Without it, a container could see, read or delete the host's IPC objects by their ID/key.
`CLONE_NEWIPC` gives the container its own empty set. Demo: `ipcmk -Q` on the host, then `ipcs -q` inside
the container shows nothing.

### What does a network namespace contain?
A separate copy of the whole network stack: **network interfaces, routing tables, firewall rules, and the
port/socket tables** (so two containers can both listen on port 80). A brand-new namespace contains only the
loopback `lo`, and it is DOWN. That is why `net_loopback_up()` sets `IFF_UP` with `ioctl(SIOCSIFFLAGS)`
before the program starts; without it even `ping 127.0.0.1` fails.

### What is a veth pair?
A **virtual Ethernet cable**: two network interfaces that are connected, so a packet sent into one
comes out of the other. Each end can be placed in a different namespace, which makes it the standard way to
connect a container to the host. Here: `vethh<pid>` stays on the host (10.0.0.1/24); the other end is moved into the
container's namespace and renamed `eth0` (10.0.0.2/24). Deleting one end deletes both, so when the container's
namespace dies the host end disappears too (we also delete it explicitly if setup failed half-way).

### Why must the child wait for the parent?
Only the parent (on the host) can create the veth pair and move one end into the child's namespace, and it
can only do that after the child exists (it needs the child's PID). If the child ran `exec` immediately, the program
would start before `eth0` exists (a race, with no network). So the child does its own setup, writes `R` to a
pipe, and **blocks reading** a second pipe until the parent writes `G`. The pipes also work as a failure detector:
the protocol is the same in every network mode (the parent always reads `R` and always sends one `G`/`A`). If the child dies early, the parent's read returns EOF and it reports the child's exit status; if the parent fails, it writes `A` (or closes the pipe) and
the child exits instead of running a half-configured container.

### Why run the `ip` command from the parent?
Creating a veth pair needs rtnetlink messages. Writing them by hand is long and hard to explain, while `ip` does
it in four short commands. The tradeoff: depends on iproute2 on the host, and spawns processes. We run it with
`fork` + `execvp` and check the exit status (never `system()`, which uses a shell). Loopback and the container-side
address are done in C with socket `ioctl`s inside the child, so those parts do not depend on any external program.

### Why no user namespace yet?
User namespaces map container root to an unprivileged host user. That changes file ownership and the rules
for mounts and veth setup, so it is a separate, later step. For now the container's root is the real root.

### Limitations to mention
Fixed 10.0.0.0/24 subnet (one veth container at a time), no NAT so no internet access from the container.

## Phase 3

### What are cgroups, and how do they differ from namespaces?
A **control group** is a set of processes whose resource use the kernel counts and limits.
- **Namespaces limit what a process can _see_** (its own PIDs, hostname, mounts, network).
- **cgroups limit what a process can _use_** (how much memory, how much CPU time, how many processes).

A container needs both. Without cgroups, a process that can see only its own world can still use all the
host's memory or CPU.

### cgroup v1 vs v2
- **v1** has a separate tree per resource (`/sys/fs/cgroup/memory/...`, `/sys/fs/cgroup/cpu/...`). A process could be
  in different places in each tree, which made it messy.
- **v2** (used here) has **one unified tree** at `/sys/fs/cgroup`. Every directory is a cgroup, every file is a
  setting or statistic, and a process is in exactly one cgroup. minibox only uses `mkdir`, `rmdir`, `read` and
  `write` on these files.

### What is a controller?
A controller is the kernel part that enforces one kind of resource: `memory`, `cpu`, `pids` (others include `io`
and `cpuset`). The root's `cgroup.controllers` lists what exists. A cgroup can only use a controller its
**parent** enabled in `cgroup.subtree_control`. So minibox enables `+memory +cpu +pids` on the root, then on
`minibox/`, and the `box-<pid>` leaves get the `memory.*`, `cpu.*` and `pids.*` files.

### How the three limits work
- **`memory.max`**: a hard limit in bytes on the memory the group may use. When the group hits it, the kernel
  first tries to reclaim memory (and could swap, so we set **`memory.swap.max = 0`**). If that is not enough, the
  **OOM killer** kills a process in the group.
- **`cpu.max = "<quota> <period>"`**: in every period (100000 µs = 0.1 s) the group may run for at most *quota* µs
  of CPU time, then it is throttled until the next period. `--cpus 0.5` → `50000 100000` = half of one core.
  `--cpus 2` → `200000 100000` = up to two cores' worth.
- **`pids.max`**: the maximum number of processes (and threads) in the group. When it is reached, `fork()`/`clone()`
  fail with `EAGAIN` ("can't fork").

### Why does pids.max contain a fork bomb?
A fork bomb works by creating processes until the system's process table is full, which freezes everything.
With `pids.max = 20`, the 21st `fork()` inside the container simply fails, so the bomb stops at 20 processes. The
host and other containers do not notice. You can see this in `pids.peak` with `--stats`.

### What does the OOM killer do?
When memory cannot be freed, the kernel's **Out-Of-Memory killer** picks a process and kills it with `SIGKILL` to
free memory. With a cgroup limit this happens **inside that cgroup only**, so the host is protected. We set
**`memory.oom.group = 1`**, so the whole container is killed together instead of leaving it half-alive. minibox
reads `oom_kill` from `memory.events`, prints "container was OOM-killed" and exits with 137 (= 128 + 9, SIGKILL).

### Why the "no internal processes" rule?
In v2, a non-root cgroup that hands out controllers to its children (`subtree_control`) may not contain processes
itself. Otherwise the kernel would have to divide resources between "the processes directly here" and "the child
groups", which is ambiguous. So resources are only distributed among children, and processes live in the leaves.
That is why `minibox/` stays empty and every container gets its own leaf `minibox/box-<pid>`.
(`minibox/` itself is kept between runs. Removing it could race with another container that is just starting.)

### When is the container put in the cgroup?
After `clone()` the parent writes the child's PID into `box-<pid>/cgroup.procs` **while the child is blocked on the
sync pipe**. Only then does it send `G`. So the limits are in force before the user's program starts. Everything it
forks later is in the same cgroup automatically.

### Cleanup on Ctrl-C / SIGTERM
minibox installs handlers for SIGINT and SIGTERM. The handler records the signal and sends `SIGKILL` to the
container's PID 1 (killing a PID namespace's init kills every process in it). Then the normal path runs: reap,
stats, `rmdir` of the leaf. A cgroup can only be removed when it is empty, and dying processes take a moment, so
`rmdir` is retried for up to a second. The handler only touches two `volatile sig_atomic_t` globals and calls
`kill()`, which is async-signal-safe. These are the only globals in the program.

### Gotcha: PID 1 ignores signals it has no handler for
Inside a PID namespace, the init process (PID 1) only receives signals from inside the namespace if it installed
a handler (SIGKILL from the parent namespace always works). So `minibox run -- timeout 3 sh -c 'busy loop'` never
stops: busybox `timeout` execs the loop as PID 1, and its SIGTERM is dropped. The fix is to run it under a shell:
`sh -c 'timeout 3 ...; true'`. A real container runtime solves this with a tiny init process (like `tini`).

## Phase 4 – managing containers (run -d, ps, logs, stop, rm)

### Why is there a long-lived monitor process?
A container is only a process in some namespaces. Somebody has to stay behind as its **parent**: only a parent can
`wait()` for the child and learn its exit code, and somebody has to remove the cgroup and the veth after it ends.
With a foreground `minibox run` that somebody is the `minibox` process itself. With `run -d` the command line must
return at once, so `minibox` forks a **monitor** that carries on in the background: it starts the container, waits for
it, then writes `status=exited`, the exit code and the finish time, and cleans up. Without it, a background container
would end with its exit code lost and a leaked cgroup and veth.

### What does "daemonizing with setsid" mean?
A daemon is a background process with no link to the terminal it was started from. The monitor does four things:
1. `fork()` – the parent returns to the shell, the child continues (and is adopted by init when the parent exits).
2. `setsid()` – starts a **new session** with no controlling terminal. Ctrl-C and the hang-up signal sent when you close
   the terminal go to the terminal's foreground process group, so they no longer reach it.
3. stdin from `/dev/null`, stdout and stderr to the log file (opened `O_APPEND`, so writes always go to the end).
   The container inherits these, which is how its output ends up in `minibox logs`.
4. `chdir("/")` so it does not keep a directory busy.
The parent waits on a pipe until the monitor reports that the container started, and then prints only the ID.

### Why does PID 1 ignore SIGTERM?
The first process of a PID namespace is its init, and the kernel protects init: a signal sent from an **ancestor**
namespace (the host) is only delivered if init has **installed a handler** for it. The exceptions are `SIGKILL` and
`SIGSTOP`, which always work. A plain `sleep` or `sh` as PID 1 has no SIGTERM handler, so `minibox stop` sends SIGTERM,
nothing happens, and after the timeout it sends SIGKILL. The container then ends with exit code 137 (128 + 9).
`stop` therefore does what `docker stop` does: polite signal, wait N seconds, then force. Real runtimes put a small init
(`tini`) in front of the command that forwards signals. If the program installs a handler (e.g. `trap`), SIGTERM works.

### How can separate minibox commands find a container?
They are different processes, so they share only the filesystem. Each container has a directory
`/var/lib/minibox/containers/<id>/` with a plain text `state` file (id, name, status, PID, monitor PID, limits, ...)
and the `log`. `ps`, `logs`, `stop`, `rm` just read those files; `stop` then sends a signal to the PID stored in the file.
The state file is **replaced atomically** (write a temp file, then `rename`), so a reader never sees half a file.
The ID is claimed with `mkdir` (atomic: it fails if it exists), and a `flock` lock makes "check the name, pick an ID, pick
a free subnet" one step, so two `run` commands at once cannot get the same name or subnet.

### What happens when the monitor dies?
Say somebody does `kill -9` on it. Nothing records the exit and nothing cleans up. The container itself is **not** killed
(only the death of its own init would do that), so it keeps running, and its cgroup and veth stay. The state file still
says `running`, but `kill(monitor_pid, 0)` fails (`ESRCH`; a zombie is also treated as dead), so `ps` shows **dead**.
`minibox rm` then kills the leftover init, removes `box-<id>`, deletes the host veth by name and deletes the state.
Before killing, it checks in `/proc/<pid>/status` (`NSpid:`) that the PID is really the init of a PID namespace, so a
reused PID of an unrelated process is never killed.

### Why does every veth container get its own subnet?
The host end of a veth pair needs an IP in the container's subnet. If two containers both used 10.0.0.0/24 the host
would have the same address on two interfaces and routing would be ambiguous. So container n gets host 10.0.n.1 and
container 10.0.n.2 on 10.0.n.0/24, where n is the lowest number no running container uses. One container alone still
gets 10.0.0.x. The interface names (`vethh<pid>`) were already unique because a PID is.

### Why is the exit recorded after the cleanup?
So that `stop` can trust the state: as soon as `status` is `exited`, the cgroup and the veth are already gone.
