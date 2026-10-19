# src/monitor.c

## Purpose
The **monitor** is the long-lived process behind a container: it stays as the container's parent, waits for it, and
afterwards records how it ended and cleans up. In a foreground `minibox run` the monitor is the `minibox` process
itself. With `run -d` it is a background process that outlives the command that started it.

Why it must exist: only a parent can `wait()` for a child's exit status, and nobody else is there to remove the
cgroup and the veth when the container ends. The container itself cannot do it (it is gone by then).

## What the monitor does (detached)
```
minibox run -d ...                              monitor (child of the above)
  state_create()  -> <state>/<id>/state
  create the log file (O_APPEND)
  pipe + fork() ------------------------------> setsid()                  no controlling terminal
  wait on the pipe                              stdin <- /dev/null, stdout+stderr -> log, chdir("/")
                                                monitor_pid = getpid(); save state
                                                container_run(): clone, cgroup, veth, release child
  <---------- one byte 'S' ------------------   on_started(): pid, started_at saved, 'S' written
  print the ID, exit 0                          waitpid(container) ... cgroup + veth cleanup
                                                record status=exited, exit_code, finished_at
                                                exit
```
If the monitor dies before the container started, the parent reads EOF instead of `'S'`, waits for it, copies the log
to stderr, removes the state and exits 1. A container that starts and then fails (e.g. command not found, exit 127)
still gets its ID printed: it did start.

The exit is recorded **after** `container_run()` returns, i.e. after the cgroup and veth were removed. So whoever sees
`status=exited` (`stop`, the tests) can rely on the resources being gone. `exit_code` is the container's exit status
(137 = killed by SIGKILL, 143 = SIGTERM); 125 means the runtime itself failed (`container_run` returned -1).

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int monitor_run_foreground(cfg, st)` | config, registered state | `container_run`'s result (-1 on failure) | runs the container with the `on_started` hook, then `state_remove` (like `--rm`) |
| `int monitor_start_detached(cfg, st)` | config, registered state | 0 started, 1 failed (message, log shown, state removed) | opens the log, pipe, `fork`; parent waits for the start byte |
| static `on_started(pid, arg)` | init PID, `struct start_ctx` | – | stores `pid`, `started_at`, saves state, writes `'S'` and closes the pipe |
| static `tell_parent_started(fd)` | pipe fd | – | writes `'S'` with `SIGPIPE` ignored for the duration (a vanished parent must not kill the monitor) |
| static `run_container(cfg, st, ready_fd)` | – | result | installs the hook, calls `container_run`, closes the pipe on every path |
| static `record_exit(st, result)` | – | – | `status=exited`, `exit_code` (-1 → 125), `finished_at`, save |
| static `monitor_main(cfg, st, ready_fd, log_fd)` | – | never returns | `setsid`, `redirect_stdio`, `chdir("/")`, save own PID, run, `record_exit`, `_exit` |
| static `redirect_stdio(log_fd)` | – | 0 / -1 | `dup2` onto fds 0, 1, 2 |
| static `wait_for_start(fd)` | – | 1 started / 0 EOF / -1 | blocking `read`, retries `EINTR` |
| static `report_failed_start(monitor, st)` | – | – | `waitpid`, copy log to stderr, `state_remove` |
| static `open_log(st)` | – | fd / -1 | `open(O_WRONLY\|O_CREAT\|O_APPEND, 0600)` |

## System calls used and why
- `fork` – the monitor must be a separate process that outlives the command line.
- `setsid` – new session and process group, no controlling terminal: Ctrl-C and terminal hang-up (`SIGHUP`) no longer reach it. This is the core of "daemonizing". (A forked child is never a group leader, so `setsid` succeeds.)
- `dup2`, `open("/dev/null")` – detach from the terminal's file descriptors; stdout/stderr go to the log. `O_APPEND` makes every `write` go to the end, so the monitor's own messages and the container's output never overwrite each other.
- `pipe2(O_CLOEXEC)` – start report to the parent. `O_CLOEXEC` closes it in the container when it `exec`s.
- `chdir("/")` – do not keep the starting directory busy.
- `sigaction(SIGPIPE)` – ignore it only around the one `write`.
- `waitpid` – the parent reaps a monitor that failed early.

## OS concept
Parent/child relationship and `wait`; process sessions and daemonizing; orphaned processes being adopted by init;
a pipe as a start-up report; why a supervisor outlives the client command.

## What if the monitor dies?
Nothing records the exit and nothing cleans up. The container's init keeps running (it is only killed if the PID
namespace init dies), its cgroup and veth stay. `state` still says `running`, but `kill(monitor_pid, 0)` fails, so
`ps` shows **dead**. `minibox rm` then kills the surviving init, removes `box-<id>` and the host veth, and deletes the state.

## How to test
```sh
sudo tests/phase4_test.sh
sudo ./minibox run -d -- sleep 100      # prints an ID; the shell returns immediately
sudo ./minibox ps                       # monitor_pid is in the state file: cat /var/lib/minibox/containers/<id>/state
```
