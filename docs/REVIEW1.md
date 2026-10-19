# Review 1 guide

## 1. What was built
| Phase | What | Files |
|-------|------|-------|
| 0 Setup | Project skeleton, environment check (kernel, WSL2, cgroup v2, userns, tools), Alpine rootfs download, Makefile | `CLAUDE.md`, `scripts/check_env.sh`, `scripts/get_rootfs.sh`, `Makefile` |
| 1 Isolation | `clone()` with PID, UTS and mount namespaces; child is PID 1; `pivot_root` into the Alpine rootfs; `/proc`, `/dev`, `/tmp` mounted | `src/main.c`, `src/container.c`, `src/rootfs.c` |
| 2 IPC + network | IPC and network namespaces; loopback via `ioctl`; optional `--net veth` (host 10.0.0.1 ↔ container 10.0.0.2); parent/child sync pipes | `src/net.c`, `src/container.c` |
| 3 Cgroups v2 | `--memory`, `--cpus`, `--pids`, `--stats`; per-container cgroup, OOM → exit 137, cleanup also on Ctrl-C/SIGTERM | `src/cgroup.c`, `src/container.c`, `src/main.c` |

Tests: `tests/phase1_test.sh`, `phase2_test.sh`, `phase3_test.sh` (run with sudo). Docs for every file are under `docs/`.

## 2. Architecture
```
 sudo ./minibox run --memory 64M --net veth -- /bin/sh
          |
      main.c  parse + validate args, check root and rootfs
          |
    container.c ── clone(NEWPID|NEWUTS|NEWNS|NEWIPC|NEWNET) ──┐
          |  parent (host)                child (PID 1 in new namespaces)
          |                                  sethostname, lo up
          |                                  rootfs.c: private mounts, bind,
          |                                    /dev, pivot_root, /proc, /tmp
          |   <────────── 'R' (ready) ──────  
   cgroup.c: mkdir box-<pid>, write limits,
             pid -> cgroup.procs
   net.c:    veth pair, peer -> child's netns
          |   ────────── 'G' (go) ────────>  eth0 = 10.0.0.2/24, execvp(command)
          |
     waitpid → stats → OOM check (137) → rmdir cgroup, delete veth
```

## 3. Demo flow (`sudo scripts/demo_review1.sh`, about 5 minutes)
| Step | What to say | Time |
|------|-------------|------|
| 1 Host vs container | Different namespace IDs and hostname | 0:30 |
| 2 Inside | PID 1, own `ps`, Alpine `/`, no host `/home` | 0:40 |
| 3 Network | Only `lo`; veth ping to 10.0.0.1 | 0:40 |
| 4 IPC | Host queue invisible inside | 0:30 |
| 5 Memory | 32M limit → OOM kill, exit 137 | 0:40 |
| 6 PIDs | Fork bomb stops at 20 processes | 0:30 |
| 7 CPU | 0.5 core → about 1.5 s CPU in 3 s | 0:30 |
| 8 Cleanup | No `box-*`, no `vethh*` | 0:20 |

Before presenting: `make`, `make rootfs` (once), `sudo tests/phase3_test.sh` as a dress rehearsal. Use `--auto` to record a backup video.

## 4. Likely viva questions
1. **What is a namespace?** A kernel feature that gives a process its own private view of one global resource (PIDs, hostname, mounts, network, IPC). The kernel is shared; only the view differs.
2. **Namespaces vs cgroups?** Namespaces limit what a process can *see*; cgroups limit what it can *use* (memory, CPU, processes).
3. **Why is the first process PID 1?** `CLONE_NEWPID` starts a fresh PID numbering and the first process gets 1 (the host sees its real PID). If PID 1 dies, the kernel kills everything in that namespace.
4. **Why `pivot_root` and not `chroot`?** `chroot` only changes where `/` resolves and can be escaped; `pivot_root` swaps the mount namespace's root and we unmount the old one, so the host filesystem is gone.
5. **Why make mount propagation private?** The host's `/` is shared on systemd/WSL, so our mounts and unmounts would propagate back to the host. `MS_REC|MS_PRIVATE` cuts that link.
6. **What does the IPC namespace isolate?** SysV message queues, shared memory and semaphores (plus POSIX queues). A queue made with `ipcmk -Q` on the host is invisible inside.
7. **What is a veth pair?** A virtual Ethernet cable: two connected interfaces that can sit in different network namespaces. One end stays on the host (10.0.0.1), the other moves into the container as `eth0`.
8. **Why does the child wait for the parent?** Only the parent can create the veth, put the PID in the cgroup and move the interface, and it needs the child's PID. Two pipes make the child block until the parent says go, so limits and network exist before `exec`.
9. **How do `memory.max`, `cpu.max` and `pids.max` work?** `memory.max` is a hard byte limit (over it, the OOM killer fires inside the group); `cpu.max` is "quota period" (50000 100000 = half a core); `pids.max` makes `fork()` fail beyond N, which contains a fork bomb.
10. **Why the "no internal processes" rule?** A cgroup that hands controllers to children can't hold processes itself, otherwise resource sharing between its own processes and its children is ambiguous. So processes live only in the `box-<pid>` leaves.

More detail: `docs/VIVA_NOTES.md`.

## 5. Current limitations (be upfront)
- **Root only.** No user namespace: the container's root is the real root with full capabilities, so this is isolation for learning, not a security boundary (no seccomp, no capability dropping, `/proc` not masked).
- **No image format.** The rootfs is one Alpine directory used in place: changes persist and concurrent containers share it. No layers/overlay, no registry.
- **Networking is minimal.** Fixed 10.0.0.0/24, so one `--net veth` container at a time; no NAT, no DNS, no internet inside the container. The veth is created by running the host `ip` command (a documented tradeoff against hand-written netlink).
- **No cgroup namespace:** the container can see its host cgroup path. The parent `/sys/fs/cgroup/minibox` is kept between runs.
- **PID 1 is the user's command.** There is no init/reaper (like `tini`), so zombies are not reaped and a PID 1 without a SIGTERM handler ignores SIGTERM sent from inside the container.
- **Orphans are not reaped.** Because PID 1 never calls `wait()`, orphans reparented to it (e.g. after a killed `minibox exec`) stay as zombies, so long-running containers can accumulate them. They are not running and not in `cgroup.procs`, but `pids.current` counts them. An optional `--init` flag (a small reaping PID 1) is future work.
- **No CLI yet:** `ps`, `stop`, `exec`, `logs`, the Flask UI and benchmarks are planned for later phases; containers run in the foreground.
- **WSL2 notes:** needs cgroup v2 (`scripts/check_env.sh` checks it); `/dev` nodes are created with `mknod`, falling back to bind-mounting the host's if refused; CPU percentages are noisy under WSL2 scheduling, so the cpu test uses a wide tolerance (35–65 %).
