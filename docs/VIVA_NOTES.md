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

## Phase 4 – managing containers (run -d, ps, logs, stop, rm, exec)

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

### What does `setns` do?
`setns(fd, nstype)` moves the **calling process** into an existing namespace, where `clone`/`unshare` create a new one.
The fd refers to the namespace; `nstype` (e.g. `CLONE_NEWNET`) makes the kernel check that it really is that type. It needs
`CAP_SYS_ADMIN` (root), and for the mount namespace also `CAP_SYS_CHROOT`. `exec` calls it for IPC, UTS, network, PID and mount.

### How do `/proc/<pid>/ns/*` work?
Each process has one entry per namespace kind in `/proc/<pid>/ns/` (`ipc`, `uts`, `net`, `pid`, `mnt`, ...). They look like
symlinks (`net -> net:[4026532589]`, the number is the namespace's inode), but opening one gives a file descriptor for the
namespace itself. Two processes with the same inode number share the namespace. An open descriptor also keeps the
namespace alive even after all its processes are gone. We use the container's **init** PID from the state file, because the
init is guaranteed to be inside every namespace the container has. `/proc/<pid>/root` is the same idea for the root directory.

### Why does joining the PID namespace require a fork?
A process's PID is fixed when it is created and is shown in its PID namespace; changing the namespace of a running process
would change its own PID underneath it. So `setns(CLONE_NEWPID)` does not move the caller: only **children created
afterwards** are placed in that namespace (the caller's `pid_for_children` changes, not its own `pid`). That is why `exec`
calls `setns(pid)` in the parent and then `fork()`s: the child is the first process that really lives in the container's PID
namespace and gets a small container PID (visible with `sh -c 'echo $$'`).

### Why must the cgroup be joined before the mount namespace?
The cgroup tree is a host file system path (`/sys/fs/cgroup/minibox/box-<id>/cgroup.procs`). After `setns(mnt)` the process sees
the container's mount table, where `/sys/fs/cgroup` is not the real cgroup tree (inside the container it is missing or something else),
so the path would not resolve. Same reason `exec` opens all `/proc/<pid>/ns/*` descriptors up front: after the mount namespace
switch, `/proc` is the container's own, which does not show the host PID. Order: open descriptors → cgroup → ipc/uts/net → **mnt last** → chroot → exec.
Writing the PID to `cgroup.procs` puts the process under the container's memory/cpu/pids limits (and OOM group), which is why
`pids.current` rises while an `exec`'d command runs.

### How does `exec` differ from `run`?
`run` **builds** a container: `clone` with five `CLONE_NEW*` flags, `pivot_root` into the rootfs, mount `/proc` and `/tmp`, set the hostname,
create the cgroup and veth, and the command becomes PID 1 with a monitor process that cleans up afterwards. `exec` builds nothing:
it needs a running container, joins its existing namespaces and cgroup with `setns`, and starts one more process (not PID 1; it
dies without affecting the container, and the container's `exit_code` is unchanged). `exec` uses `fchdir`+`chroot` on the saved root,
not `pivot_root`, because the mount namespace is already set up. It also has no cleanup duty: the container's monitor still owns the
cgroup and veth. Like `docker exec`, the exec'd process dies with the container, because killing the init kills every process in the PID namespace.

### What happens to a command started by `exec` if `minibox exec` is killed?
It becomes an **orphan**. In Linux a process whose parent dies is adopted by another process: normally PID 1, but inside a PID namespace that is the
**namespace's own init**, i.e. the container's PID 1 (`sleep` or `sh`). The orphan keeps running in the container and in its cgroup, so it still counts
against `pids.max`. The container's init does not even have to reap it quickly; if it is a plain `sleep` it never calls `wait`, so the orphan's zombie is not
reaped either. The fix is in `exec.c`, below.

### What does `PR_SET_PDEATHSIG` do, and what are its limits?
`prctl(PR_SET_PDEATHSIG, SIGKILL)` tells the kernel: "when my parent process dies, send me SIGKILL". The child calls it right after `fork`, and the setting
survives `execve`, so it applies to the command. Limits: (1) it is **not inherited** by the command's own children, so a grandchild is not covered;
(2) there is a **race**: the parent may die before the `prctl` call, so the child re-checks `getppid()`: its parent is on the host, outside the
container's PID namespace, where a process cannot see it, so `getppid()` is 0 while it lives and non-zero (the container's init) once orphaned;
(3) it is cleared when the process changes credentials via a set-uid program; (4) it fires when the *thread* that created the child exits, which is fine for our single-threaded parent.

### Why does a process group let one signal reach grandchildren?
Every process belongs to a process group, and `kill(-pgid, sig)` sends the signal to every member. A child inherits its parent's group, so if the
command is made the leader of a new group with `setpgid(0,0)`, the shell it runs and every program that shell starts are in the same group, and one
`kill(-pgid, SIGKILL)` removes them all. The parent calls `setpgid(child, child)` too, because either side might run first. The signal handler in the parent
may only use async-signal-safe calls (`kill` is one; `printf` and `malloc` are not), so it stores the signal number and kills; the main code then reaps
the child and exits with 128 + the signal. A program can leave the group (`setsid`/`setpgid`), and **SIGKILL of `minibox exec` cannot be forwarded**
because nothing can run after SIGKILL, so a grandchild can survive that one case (the direct child still dies through PDEATHSIG).

### Why are interactive terminals handled differently?
With a terminal (`exec -- sh`), the shell must stay in the terminal's **foreground process group**. A process in another group that reads the terminal
gets SIGTTIN and is stopped; Ctrl-C is delivered to the foreground group, so it would no longer reach the shell. So when `isatty(0)` is true, `exec` keeps the old
behaviour (same group, ignore SIGINT/SIGQUIT while waiting). When stdin is a pipe or `/dev/null` (scripts, the web UI) there is no terminal and no job control, so
the own-group and signal-forwarding approach is safe.

## Phase 5 – the web UI

### What is the UI, in one sentence?
A small local web page (Flask, Python) that lets you launch, list, stop, remove, read the logs of and run commands in containers. It only
manages containers: no live graphs, no monitoring. The project is still a container runtime, not a monitoring tool.

### Why does the UI drive the CLI instead of reimplementing it?
The runtime logic (namespaces, cgroups, state files, cleanup) already exists and is tested in C. If the UI had its own copy in Python, there
would be two implementations that can disagree. So every button is one call of `minibox run -d`, `ps`, `logs`, `stop`, `rm` or `exec`.
The CLI is the single source of truth; the UI is a thin layer on top that can be replaced without touching the runtime.

### What is `ps --json` for?
`ps` prints a table for people. A program should not parse a table with spaces and truncated columns. `ps -a --json` prints the same data
as a JSON array (ID, name, status, PIDs, exit code, times, command, rootfs, hostname, net, limits, IP), so the UI reads it with
`json.loads`. Status `dead` is computed by the CLI at read time (the monitor process is gone but the file still says `running`).

### Why is `shell=True` never used?
With `shell=True` the command line goes to `/bin/sh`, which interprets `;`, `|`, `$(...)`, backticks and newlines. A name like
`x; rm -rf /` would run a second command as root. The UI instead builds an argument **list** (`[binary, "run", "-d", "--name", name, ...]`)
and `subprocess` calls `execve` directly, so each value is exactly one argument and nothing is interpreted. The user's command is split
with `shlex.split` and placed after `--`, so `; rm -rf /` becomes the four literal arguments `;`, `rm`, `-rf`, `/` for `execvp`.
Validation on top of that is defence in depth: a whitelist for every field, and the rootfs path is chosen by the server, never by the browser.

### What is the security model?
The UI runs as root (minibox needs it) and can run commands, so:
1. It listens on `127.0.0.1` only; a non-loopback `--host` is refused. Other computers cannot connect.
2. A random token is generated at every start and printed once. Every `/api` request must send it as `Authorization: Bearer ...`,
   compared with `hmac.compare_digest` (constant time, so response time does not leak how much matched). The page takes it from the URL,
   keeps it in `sessionStorage` and removes it from the address bar.
3. The `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`. This blocks DNS rebinding: a malicious site could make its own
   hostname resolve to 127.0.0.1 and have your browser talk to the UI; the Host header would then show the attacker's name.
4. No CORS headers, so other web sites cannot read answers. The token is a header, not a cookie, so a hostile page cannot make your browser
   send it (no CSRF). CSP `default-src 'self'` and `Referrer-Policy: no-referrer` keep the page self-contained and the token out of referrers.
5. Strict validation of every field, a 16 KB body cap, at most 10 running containers, 10 s limit on `exec`, container output escaped on the page
   (`textContent`, never `innerHTML`).

### What are the limits of this model?
- **A container is not a hard security boundary.** It shares the host kernel, runs as root without a user namespace, and has no seccomp filter,
  no dropped capabilities, no read-only `/proc` paths. A malicious program inside might escape, and anyone who can use the UI can run commands
  as root on the host. That is why it must stay on localhost and why the token URL must be kept private.
- The token protects against other web pages and other users who do not know it. It does not help if the machine is already compromised, and it
  travels over plain HTTP (acceptable on loopback; it would not be on a network).
- Flask's built-in server is for development; fine for one local user, not for the internet.
- `exec` time limit: killing the host-side `minibox exec` client would not stop the process inside the container (it is in another PID namespace),
  so the command is wrapped with busybox `timeout -s KILL 10` inside the container.

### Why does `logs` read through the CLI and tail in Python?
`minibox logs` prints the whole log and has no `--tail`. The UI reads at most 4 MB of it and keeps the last N lines (default 200), so a runaway
log cannot fill the server's memory.

### How does the exec time limit work, and why two layers?
The UI runs `minibox exec <id> -- timeout -s KILL 10 <command>`: the busybox `timeout` inside the container kills a long command after 10 s, so the
normal timeout does not depend on the host side at all. As a second layer, if `minibox exec` itself is still running after 13 s (or the output is too big), the
UI sends it SIGTERM; `minibox exec` then kills the command's whole process group and exits with 143. If it does not exit within 2 s the UI sends SIGKILL, and
the kernel kills the command's direct child through `PR_SET_PDEATHSIG`. Killing only the client, as the first version did, would leave the command running
as an orphan under the container's PID 1 (see Phase 4). Remaining gap: after SIGKILL a grandchild may survive until it ends or the container is stopped.

### What is a zombie process, and why does minibox leave some behind?
A **zombie** is a process that has already exited but whose entry in the process table is still there, because its parent has not yet called
`wait()` to collect its exit status. It uses no CPU or memory beyond that entry, and it shows as `<defunct>` (state `Z`) in `ps`. It cannot be killed
(it is already dead); only its parent reaping it, or the parent dying, removes it.

**Orphans and the PID namespace's init.** When a parent dies before its children, the kernel must give the children a new parent. Inside a PID
namespace that is the namespace's PID 1 (outside, it would be the host's init). So when `minibox exec` kills a command's process group, the surviving
children of the dead shell are reparented to the container's PID 1.

**Why a PID 1 that never waits accumulates zombies.** On a normal system PID 1 loops on `wait()` and reaps every orphan. In minibox, PID 1 is just the
user's command (`sleep 300`), which never calls `wait()`. Every orphan that later dies stays a zombie until the container ends. A long-running container
can therefore fill its process table slots (and hit `--pids`).

**`pids.current` vs `cgroup.procs`.** `pids.current` counts every task that still exists, zombies included. `cgroup.procs` lists only live processes.
After SIGKILL of `minibox exec` the orphaned direct child may or may not be left as a zombie under the container's init, depending on timing and process layout; the test reports this (`INFO`) without failing and only asserts that the live count returns to baseline.
That is why, after a killed `exec`, `pids.current` can be 2 while `cgroup.procs` shows only the init: the extra one is a zombie. The tests therefore
count the lines of `cgroup.procs` (see `tests/lib.sh`) to ask "is anything still running?".

**How Docker solves it.** `docker run --init` starts a tiny init program (`tini`) as PID 1 and runs the user's command as its child. `tini` forwards
signals and calls `wait()` in a loop, so orphans are reaped. minibox has the same thing as an opt-in: `minibox run --init` (see "Phase 7 – the `--init` option" below).

## Phase 6 – benchmarks and limit enforcement

### What does each benchmark prove?
- **Startup time** compares how long it takes to run `/bin/true` bare, in plain namespaces (`unshare`), in minibox without and with limits, with a veth network, and (if present) in Docker. The steps add up: namespaces cost a little, `pivot_root` and cgroups cost more, veth setup costs most. It shows where minibox's time goes, not that it is "faster than Docker".
- **Memory** shows what one more container costs: `memory.current` of its cgroup, the PSS of its init, and the PSS of the monitor process outside the cgroup. Running 1, 5 and 10 containers makes the cost per extra container visible (the slope), separate from fixed costs.
- **Limits** prove the cgroup limits really work, with numbers from the kernel instead of "it seemed to work".

### Why interleave the runs and discard warm-up runs?
Computers drift: the CPU speeds up or slows down, caches fill, other programs wake up. If all runs of one configuration came first, the drift would look like a difference between configurations. Shuffling the order spreads the drift over all of them. The first runs are slow because the binary, rootfs files and libraries are not in the page cache yet; those warm-up runs are thrown away so every measured run sees the same warm state.

### Why median and p95 instead of the mean?
A few slow runs (the OS was busy) pull the mean up. The median is the typical run and ignores those. p95 says "95 % of runs were at least this fast", so it shows how bad the slow tail is. The mean and standard deviation are still in the CSV for completeness.

### How do the cgroup files show the limits working?
- `memory.events` has an `oom_kill` counter: at least 1 means the kernel killed the process for hitting `memory.max`. `memory.peak` is the highest usage ever; it stays near the limit and does not go far above it. A control run with a bigger limit shows the same workload really needs more.
- `pids.peak` is the highest number of processes in the cgroup; with `pids.max=20` it cannot exceed 20, and the extra `fork()` calls fail with `EAGAIN`.
- `cpu.stat` `usage_usec` is the CPU time used. Divided by the wall-clock time it gives the CPU share: with `cpu.max` of 50000/100000 a busy loop gets about 0.5. An unthrottled control gets about 1.0.
- minibox prints these with `--stats` just before it removes the cgroup, because the cgroup disappears with the container.

### Why can't the benchmark numbers be compared directly to Docker?
Docker has a daemon, a different filesystem (overlayfs), more namespaces and security settings (seccomp, capabilities), and a client-server round trip. WSL2 is also a virtual machine, so timings are not bare-metal numbers. The Docker row is a rough reference only.

### Why did a benchmark workload hang the container? (the PID 1 lesson)
The CPU test ran `sh -c "timeout 2 sh -c '...busy loop...'"`. busybox `sh -c` **execs the last command** instead of forking it, so `timeout` replaced the shell and became PID 1 of the container. `timeout` kills its child after 2 seconds and then ends itself by sending itself a signal. That is the problem: **the PID-namespace init is special**. The kernel drops signals sent to it from inside its own namespace unless the program installed a handler for them (only signals from the parent namespace, such as SIGKILL from the host, always work). `timeout` has no handler, so it could not kill itself and the container never ended. A normal process has no such protection: the same signal would just kill it. Appending `; exit 0` keeps the shell as PID 1 (it forks `timeout`, waits for it, and exits normally).

How it relates to other things: the same "PID 1 is special" rule is why `sleep 300` as PID 1 ignores SIGTERM (so `stop` waits for its grace period before SIGKILL, and the benchmark cleanup writes to `cgroup.kill` instead), and why orphaned zombies pile up under minibox's PID 1 (PID 1 inherits orphans and is expected to `wait()` for them). Docker's `--init` runs a tiny init (`tini`) as PID 1 that has signal handlers and reaps children, which avoids all three problems. minibox now has the same opt-in: `minibox run --init` (see "Phase 7 – the `--init` option").

**The fix was in the benchmark, not the runtime**: minibox behaves exactly like a real container runtime here. The benchmark now keeps a shell as PID 1, gives every call a timeout, and records `ERROR`/`TIMEOUT` rows instead of aborting.

## Phase 6 results – reading the numbers
The numbers are in `docs/BENCHMARKS.md` (tables and analysis) and come from `bench/results/reference/`. Docker was not measured, so never quote a Docker figure.

### How to read the numbers
- **Startup** is the time from launching the command to its exit, in milliseconds. Read the **median** (the typical run) first, then **p95** (the slow tail). Compare configurations as ratios.
- **Memory** is bytes per container. The real minibox overhead is the monitor process plus the cgroup's memory; the host `sleep` is a reference only.
- **Limits** are expected-versus-measured pairs with a status; OK means the kernel behaved as expected.

### What to be able to explain about each chart
- **`startup.svg`:** five bars (plain process, `unshare`, minibox without limits, with limits, with veth). The y axis is a **log scale**. Be able to say that the first three are close (so rootfs setup adds little on top of namespaces), that the limits bar is higher at the median but has the same minimum (observed, cause not investigated), and that veth is highest (likely the `ip` commands). The whisker is p95. Docker is shown as "not measured".
- **`memory.svg`:** bars for 1, 5, 10 containers: cgroup memory, init PSS and monitor PSS, per container. Explain why init PSS falls as k grows, and that the total (cgroup + monitor) stays about the same. The dashed host `sleep` line is a different binary, so not a fair baseline.
- **`limits.svg`:** a table of eight checks with expected, measured and status. Be able to say what each limit test did and which kernel file proves it.

### Likely viva questions
**Why the median and not the mean?** A few slow runs pull the mean up (for `minibox_nolimits` the mean is 5.733 ms and the median 3.994 ms). The median is the typical run. We still show the mean and p95 because the slow runs matter too.

**Why interleave the runs and warm up first?** The machine drifts (CPU speed, caches, other programs). Shuffling the runs spreads that drift over all configurations so it does not look like a difference between them. The first runs are slow because files are not cached yet; discarding warm-ups makes every measured run start from the same warm state.

**Why a log scale on the startup chart?** The values range from 1.716 ms to 165.379 ms. On a linear axis the small bars would be invisible slivers. On a log axis, equal distances are equal ratios. The cost is that bar heights cannot be compared by eye as differences.

**Why is the memory baseline not fair?** The host `sleep` is a different program (glibc, dynamically linked) from the container's `sleep` (busybox), so the difference says nothing about the container runtime. The fair figure is the minibox overhead: monitor process plus cgroup memory per container.

**Why does PSS per container drop with more containers?** PSS divides each shared page (the busybox binary, libc) among the processes that share it. With one container its init pays for all of it (about 0.96 MiB); with ten it pays a tenth (about 0.15 MiB). The first container pays more of the shared cost. It is expected, not a measurement error.

**What does an OOM kill look like in `memory.events`?** The `oom_kill` counter is 1 or more (3 in the reference run), `memory.peak` stays at the limit (64.0 MiB for a 64M limit), and the container exits with code 137 (SIGKILL). The control run with a bigger limit exits 0, which shows the limit caused the kill.

**Why is startup with veth slower?** The likely reason is that the setup runs several `ip` commands as separate processes and moves interfaces between network namespaces. This is the likely reason, not something we measured.

**Why is `minibox_limits` slow at the median when its minimum is the same?** Observed and unexplained: most runs have an extra delay of roughly 30 ms. We did not investigate, so we do not claim a cause; hypotheses are cgroup creation or removal cost and waiting on teardown.

## Phase 7 – the `--init` option (a tiny PID 1)

### What is special about PID 1?
Every PID namespace has a first process, PID 1, and the kernel treats it differently from all others:
1. **It adopts orphans.** When a process dies, its children need a new parent; inside the container that is PID 1. A dead child stays a *zombie* until its parent calls `wait()`. A PID 1 that never waits (like `sleep 300`) collects zombies.
2. **It is protected from signals.** A signal sent to PID 1 from *outside* its namespace (the host) is dropped unless PID 1 has installed a handler for it (`SIGKILL` and `SIGSTOP` always work). Signals from inside are dropped the same way. So a plain `sleep` or `sh` as PID 1 ignores SIGTERM, and a program that becomes PID 1 (busybox `timeout`) cannot kill itself.
3. **If it exits, the whole namespace is killed.**

### What does `--init` do, step by step?
Without `--init` the command replaces our setup process and *is* PID 1. With `--init` the setup process stays PID 1 and becomes a tiny init (`src/minit.c`):
1. It renames itself `minibox-init` (`prctl(PR_SET_NAME)`) so `/proc/1/comm` and `ps` show what it is.
2. It **blocks** SIGCHLD, SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2. A blocked signal is not dropped; it waits as *pending*. That is how a handler-less PID 1 can still receive SIGTERM from the host.
3. It `fork`s. The child puts the signal mask and handlers back to normal and `exec`s the real command (PID 2). If the program does not exist: exit 127; if it cannot be run: 126; if `fork` fails: message and 127.
4. It loops: first **reap**, then **wait for a signal** (`sigtimedwait`). A SIGCHLD only wakes it up; any other signal is **forwarded** to the command with `kill()`.
5. When the command has been reaped, the init exits with its status: the exit code, or 128+signal if it was killed (143 for SIGTERM, 137 for SIGKILL). `minibox run` returns that code and `stop` records it.

### Why `waitpid(-1)` in a loop?
`waitpid(pid)` waits for one particular child. Orphans are children of the init that it never forked itself, so it does not know their PIDs: `waitpid(-1, ..., WNOHANG)` means "any child that has exited, but do not block". It is called repeatedly until it returns 0 (children exist but none has exited) or fails with `ECHILD` (no children), because SIGCHLD is not queued: three children dying together may produce only one signal, and a single `waitpid` would leave two zombies. The loop also reaps first and waits second, and `sigtimedwait` has a 1 s timeout, so even a lost SIGCHLD cannot leave a zombie for long.

### Why forward signals?
With `--init` the command is no longer PID 1, so a signal that `minibox stop` sends to "the container's PID" goes to the init, not to the program. The init passes it on, the program (a normal process, with default signal handling) simply dies of SIGTERM, and `stop` returns quickly with exit code 143 = 128+15 instead of waiting out the 5 s grace period and then using SIGKILL (137). Signals the terminal generates (Ctrl+C) are not forwarded, because the command is in the same foreground process group and already receives them directly; forwarding would deliver them twice.

### How does it relate to Docker's `--init` and `tini`?
It is the same idea: `docker run --init` puts a small program called `tini` (it is also what Kubernetes people mean by "an init in the container") in front of the command. `tini` forwards signals and reaps children. minibox's `minit.c` is a few dozen lines doing the same two jobs. It is much simpler than a full init system (systemd): it starts nothing but the one command and supervises nothing.

### What is the trade-off?
One more process per container (a few hundred KB of memory, one entry against `--pids`, so `--pids` must be at least 2), and one more step between `stop` and the program. That is why it is **opt-in**: the default stays "the command is PID 1" (the Phase 1 test relies on it), and you add `--init` for containers that start child processes, are killed by `exec` timeouts, or must stop quickly. The web UI has a checkbox for it. Interactive `run --init -- sh` is designed to keep working because the init does not touch the terminal's process groups (this path is reasoned about, not covered by the automated tests, which have no tty).

### How is it tested?
`sudo bash tests/init_test.sh`: PID 1 is `minibox-init` and the command is PID 2 (control: without `--init` the command is PID 1); an orphan is reaped (0 zombies; control without `--init` shows 1); exit codes 7, 143, 137, 127, 126 are propagated; `stop` takes under 2 s and gives 143 (control: grace period then 137); the earlier benchmark hang (`timeout` as PID 1) cannot happen; limits, `--net veth` and `exec` work; nothing is left behind.
