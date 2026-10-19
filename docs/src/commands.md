# src/commands.c

## Purpose
The five subcommands of the CLI: `run`, `ps`, `logs`, `stop`, `rm`. Each has its own argument parsing and usage
message (printed to stderr, exit status 1). They are separate short-lived processes: they never talk to a monitor
directly, they read its state files (`src/state.c`) and send signals to the container's init.

## Usage
```
minibox run  [-d] [--name NAME] [--rootfs DIR] [--hostname NAME] [--net none|veth]
             [--memory N[K|M|G]] [--cpus X] [--pids N] [--stats] -- COMMAND [ARGS...]
minibox ps   [-a] [--json]
minibox logs [-f] <id|name>
minibox stop [--time N] <id|name>
minibox rm   [-f] <id|name>
```

## Behaviour
- **run** – parse (`options.c`) → must be root → validate rootfs → register state (`state_create`) → foreground:
  `monitor_run_foreground` (state deleted at the end, exit status = the command's) / `-d`: `monitor_start_detached`,
  then print only the 12-hex ID. Foreground containers are registered too, so `ps` sees them while they run.
- **ps** – table `ID NAME STATUS PID COMMAND CREATED LIMITS`, or `--json`. Default lists `running` **and `dead`**
  containers (a dead one still holds an init, cgroup and veth, so hiding it would hide the problem); `-a` adds `exited`.
  `dead` = stored `running`, but `kill(monitor_pid, 0)` says the monitor is gone.
  LIMITS looks like `mem=64M cpu=0.5 pids=32`, `-` if none. CREATED is `5s ago`, `3m ago`, `2h ago`, `1d ago`.
  JSON: an array of objects with all state fields (`status` is the effective one); strings are escaped (`"`, `\`,
  `\n`, `\r`, `\t`, other control characters as `\u00XX`). Bytes ≥ 0x80 are passed through unchanged, so non-UTF-8 command bytes would give invalid JSON.
- **logs** – copies the log to stdout. `-f` keeps polling every 200 ms until the container is no longer running
  (or Ctrl-C). A foreground container has no log.
- **stop** – `kill(pid, SIGTERM)`, poll the state file every 100 ms for up to `--time N` seconds (default 5), then
  `kill(pid, SIGKILL)` and wait up to 5 s more. Prints `stopped <id> (SIGTERM)` or `stopped <id> (SIGKILL after Ns)`.
  Already exited: says so, exit 0. Dead: error pointing to `rm`.
- **rm** – exited: delete state. Running: refused unless `-f`, which stops it first (same 5 s default). Dead
  (allowed without `-f`): kill the surviving init (only if `/proc/<pid>/status` shows it is a PID-namespace init), wait
  for it to vanish, remove `box-<id>`, delete the host veth by name (`vethh<pid>`), then delete the state. Prints the ID.

### Why `stop` usually needs SIGKILL
The first process of a PID namespace is its init. For signals sent from an **ancestor** namespace (the host), the
kernel delivers them to init only if init installed a handler for that signal — except `SIGKILL` and `SIGSTOP`,
which are always delivered. A container whose init is plain `sleep` or `sh` has no `SIGTERM` handler, so SIGTERM is
silently dropped and `stop` has to escalate to SIGKILL after the timeout. The container then ends with exit code
137 (128 + 9). A program with a handler (e.g. `sh -c 'trap "exit 0" TERM; ...'`) stops on SIGTERM.
Real runtimes put a tiny init (`tini`) in front of the command for this reason.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int cmd_run(argc, argv)` | args from `run` | container status / 0 for `-d` / 1 | see above; warns if `--stats` has no limit |
| `int cmd_ps(argc, argv)` | `-a`, `--json` | 0 / 1 | `state_list`; prints to stdout |
| `int cmd_logs(argc, argv)` | `-f`, key | 0 / 1 | "no such container", "has no log" |
| `int cmd_stop(argc, argv)` | `--time N`, key | 0 / 1 | invalid `--time` (0..3600) |
| `int cmd_rm(argc, argv)` | `-f`, key | 0 / 1 | refuses a running container without `-f` |
| static `usage_run/ps/logs/stop/rm()` | – | – | usage message on stderr |
| `int require_root(void)` (shared via `commands.h`) | – | 0 / -1 | `geteuid() == 0`, else "must be run as root". `run` and `exec` insist (`exec` is in `src/exec.c`): `ps`/`logs`/`stop`/`rm` simply fail with a permission error on the 0700 state directory (which also lets them be tried without sudo on a scratch `MINIBOX_STATE_DIR`) |
| static `sleep_ns(ns)` | – | – | `nanosleep` |
| static `ellipsize`, `format_age`, `format_size`, `format_limits`, `ps_visible`, `print_table_row`, `print_table` | – | – | table output; control characters in a command become spaces |
| static `json_string`, `json_str_field`, `print_json_object`, `print_json` | – | – | hand-written JSON writer |
| static `is_running(id)` | ID | 1 / 0 | reloads the state; a removed container is not running |
| static `follow_log(id, fd)` | – | 0 / -1 | checks "running" **before** each copy, so the final round still prints output written just before exit |
| static `has_stopped`, `wait_stopped(id, seconds)` | – | 1 / 0 | poll every 100 ms |
| static `send_signal(pid, sig)` | – | 0 / -1 | `kill`; `ESRCH` (already gone) is success |
| static `stop_container(st, seconds)` | state | 0 / -1 | SIGTERM → wait → SIGKILL → wait; used by `stop` and `rm -f` |
| static `stop_not_running(st, status, &ret)` | – | 1 if handled | message for exited/dead |
| static `parse_seconds(s, &n)` | `--time` value | 0 / -1 | integer 0..3600 |
| static `wait_gone(pid)` | – | – | wait up to 5 s for a killed process to vanish |
| static `clean_dead(st)` | – | – | kill init (NSpid-checked), `cgroup_remove_leftover`, `net_veth_cleanup` |
| static `prepare_removal(st, force)` | – | 0 / -1 | decides what must happen before the state is deleted |

## System calls used and why
- `kill` – SIGTERM / SIGKILL to the container's init; `kill(pid, 0)` for liveness (in `state.c`).
- `nanosleep` – polling without busy-waiting.
- `read`/`write` on fds 0..2 and the log (`state_copy_fd`) – no stdio buffering, so `logs -f` output appears immediately.
- `geteuid` – root check for `run`. Plus everything in `state.c`, `monitor.c`, `cgroup.c`, `net.c`.

## OS concept
Inter-process communication through files and signals; PID-namespace init signal semantics; process liveness,
zombies and PID reuse; cleaning up resources of a crashed supervisor.

## How to test
```sh
sudo tests/phase4_test.sh
sudo ./minibox run -d --name demo --memory 64M -- sh -c 'while :; do date; sleep 1; done'
sudo ./minibox ps ; sudo ./minibox logs -f demo      # Ctrl-C ends the follow, the container keeps running
sudo ./minibox stop demo ; sudo ./minibox ps -a ; sudo ./minibox rm demo
./minibox stop ; ./minibox rm ; ./minibox logs ; ./minibox ps -x     # usage messages (no root needed)
```
