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
