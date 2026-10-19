# src/container.c

## Purpose
Creates the container process with `clone()` in new PID, UTS, mount, IPC and network
namespaces, synchronises with the child through two pipes (so the parent can finish network
setup before the program starts), and waits for the child.

## Parent/child handshake
```
parent                                   child (PID 1 in new namespaces)
clone() ------------------------------>  sethostname, bring lo up, rootfs_setup
  wait on ready pipe (ALL modes) <----   write 'R'
  (--net veth only) net_veth_setup(pid): read go pipe  (blocks)
    create pair, move peer into child's netns
  write 'G' (or 'A' on failure) ------>  'G': set eth0 10.0.0.2/24 (veth), chdir, execvp
waitpid; net_veth_cleanup(pid)           'A' or EOF: exit 1
```
The protocol is identical in every network mode: the parent always reads `'R'` first, then (veth only) sets up
the network, then always sends one `'G'`/`'A'` byte. (Earlier bug: in `--net none` the parent never read `ready` and
closed it, so the child's write of `'R'` failed with EPIPE and the container died before running the command.)
Pipes are `pipe2(O_CLOEXEC)` so they close on `exec`. EOF on the `ready` pipe means the child died
before reaching the handshake; the parent then still reaps it and prints
"container died during setup (exit status N)", and deletes any veth. The parent keeps the `go` read end
open until after its write so the write can never raise `SIGPIPE`.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `static void *alloc_stack(void **top)` | out-pointer | mapping base or NULL | `mmap` 1 MiB (`MAP_STACK`); `perror` |
| `make_sync_pipes(p)`, `close_pipes(p)`, `close_fd(fd*)` | pipe struct | 0 / -1 | `pipe2(O_CLOEXEC)` x2; `perror` |
| `read_byte(fd, &c)`, `write_byte(fd, c)` | – | 1/0/-1, 0/-1 | retry on `EINTR`; `perror` |
| `set_hostname(name)` | hostname | 0 / -1 | `sethostname` in the UTS ns |
| `child_handshake(p)` | pipes | 0 / -1 | writes `'R'`, blocks for `'G'`; prints "aborted by parent" on `'A'`/EOF |
| `child_setup(args)` | `struct child_args` | 0 / -1 | closes parent's pipe ends → hostname → `net_loopback_up` → `rootfs_setup` → handshake → (veth) `net_set_ipv4("eth0","10.0.0.2",24)` → `chdir("/")` → PATH/HOME |
| `child_main(void *arg)` | args | exit code | 1 on setup failure, 127 if `execvp` fails, never returns on success |
| `parent_wait_ready(fd)` | fd | 0 / -1 | called in every mode; -1 on EOF (child died early) |
| `parent_network_setup(cfg, pid)` | – | 0 / -1 | veth mode only: `net_veth_setup(pid)`; 0 for `--net none` |
| `release_child(p, ok)` | – | 0 if sent `'G'` | writes `'G'` or `'A'` |
| `wait_for_child(pid)` | pid | exit code, 128+sig, or -1 | `waitpid` with `EINTR` retry |
| `int container_run(const struct container_config *cfg)` | config | child's exit status; -1 if creation or network setup failed | pipes, stack, `clone`, handshake, wait, `net_veth_cleanup` (always in veth mode, even after failure), `munmap` |

## System calls used and why
- `mmap`/`munmap` – child stack for `clone`.
- `clone(CLONE_NEWPID|CLONE_NEWUTS|CLONE_NEWNS|CLONE_NEWIPC|CLONE_NEWNET|SIGCHLD)` – fork with five new namespaces (no user namespace in this phase).
- `pipe2(O_CLOEXEC)`, `read`, `write`, `close` – the parent/child handshake.
- `sethostname`, `chdir`, `setenv`, `execvp` – as in Phase 1.
- `waitpid` – collect exit status.

## OS concept
Namespaces; process synchronisation with pipes (a pipe also signals the death of the other side via EOF).
`CLONE_NEWIPC` isolates SysV IPC (message queues, shared memory, semaphores) and POSIX message queues;
`CLONE_NEWNET` gives the container its own interfaces, routes and ports.

## How to test
```sh
sudo tests/phase1_test.sh && sudo tests/phase2_test.sh
sudo ./minibox run --rootfs rootfs -- ip link                  # only lo
sudo ./minibox run --rootfs rootfs --net veth -- nosuchcmd; echo $?   # 127, no vethh* left
```
