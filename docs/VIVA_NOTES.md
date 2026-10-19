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
