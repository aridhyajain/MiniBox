# src/state.c

## Purpose
Keeps one directory per container on disk, so that separate `minibox` commands (`ps`, `logs`, `stop`, `rm`) can find
a container that was started by another process. Also allocates IDs and per-container subnets and decides whether a
container is alive.

## State directory layout
`<state_root>` is `$MINIBOX_STATE_DIR` if set (the tests use a temp dir), otherwise `/var/lib/minibox/containers`.
Directories are created with mode `0700`.
```
<state_root>/
├── .lock                  empty file; flock() on it serialises "create a container"
└── 3fa9c01be274/          one directory per container, named by its 12-hex ID (mode 0700)
    ├── state              key=value file (below)
    └── log                stdout+stderr of a detached container (O_APPEND)
```
`state` fields, one `key=value` per line: `id, name, status (running|exited), pid, monitor_pid, exit_code,
created_at, started_at, finished_at, command, rootfs, hostname, net (none|veth), memory (bytes), cpus (cores),
pids, ip`. `0` / empty means "not set". `pid` is the container's init as seen from the host; `monitor_pid` is the
process that waits for it. `dead` is never stored, it is computed (see `state_effective_status`).
Backslash and line breaks inside values are escaped (`\\`, `\n`, `\r`) so a command can never break the format.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `const char *state_root(void)` | – | state directory path | reads the environment variable |
| `int state_path(id, file, buf, size)` | ID, file name | 0 / -1 if it does not fit | builds `<root>/<id>/<file>` |
| `int state_valid_name(const char *name)` | name | 1 / 0 | letters, digits, `_ . -`, first char alphanumeric, ≤ 63 chars, not 12 hex digits (would look like an ID) |
| `void state_init_from_config(st, cfg, name)` | run configuration, name or NULL | – | joins `argv` with spaces into `command` (truncated at 1023 bytes); copies rootfs, hostname, net, limits |
| `int state_create(st)` | filled `st` | 0 / -1 (message) | creates the root (mode 0700), takes `flock(.lock)`, checks the name is unused, picks the subnet (veth), claims a random ID with `mkdir`, writes `status=running`, `created_at`, `monitor_pid=getpid()`; releases the lock. On a failed save it removes the new directory. |
| `int state_save(st)` | state | 0 / -1 (message) | writes `state.tmp`, `fsync`, `rename` over `state` (atomic) |
| `int state_load(id, st)` | ID | 0 / -1, `errno` set, silent | reads and parses `state`; unknown keys are ignored |
| `int state_list(&arr, &n)` | – | 0 / -1 | `malloc`'d array sorted by `created_at`; unreadable entries and `.lock` are skipped; a missing root is an empty list (not an error) |
| `int state_find(key, st)` | ID or name | 0 / -1 ("no such container") | exact ID first, then exact name. The user's text is only *compared* with directory entries, never put into a path |
| `int state_remove(id)` | ID | 0 / -1 (message) | `unlinkat` every file, then `rmdir` |
| `int state_copy_fd(in, out)` | fds | 0 / -1 | `read`/`write` loop until EOF, handles `EINTR` and short writes; no stdio buffering |
| `int process_alive(pid_t)` | pid | 1 / 0 | `kill(pid, 0)` not `ESRCH`, and `/proc/<pid>/stat` state is not `Z`/`X`: an orphan that nobody has `wait()`ed for yet is a zombie and counts as dead |
| `int state_pid_is_ns_init(pid)` | pid | 1 / 0 | `NSpid:` line in `/proc/<pid>/status` has ≥ 2 numbers and the last is 1: the process is PID 1 of a PID namespace. `rm` uses it so it never kills an unrelated process that reused the PID |
| `const char *state_effective_status(st)` | state | `"running"`, `"exited"` or `"dead"` | stored `running` + `monitor_pid` not alive → `dead` |
| `int state_net_index(st)` | state | n from `10.0.n.2`, or -1 | parses `ip` |
| static `put_escaped`, `write_fields`, `finish_file` | – | – | serialise a state; `finish_file` checks `fflush`, `fsync`, `fclose` |
| static `unescape`, `set_text_field`, `set_number_field`, `parse_line` | – | – | parse one `key=value` line |
| static `slurp(path, buf, size)` | – | length / -1 | read a small file |
| static `append_entry`, `compare_created` | – | – | helpers for `state_list` |
| static `subnet_in_use`, `allocate_subnet` | – | – | lowest free `n` (0..255). Used by every state with stored status `running` whose monitor is alive; a dead one only while its host veth interface still exists, so a stale record cannot block `10.0.0.x` forever |
| static `lock_root`, `random_id`, `claim_id`, `check_name_free`, `create_locked` | – | – | the steps of `state_create`. `claim_id` makes a random ID with `getrandom` and claims it with `mkdir`; `EEXIST` means a collision, so it retries (20 times) |
| static `mkdir_p`, `dir_path` | – | – | `mkdir -p` with a given mode; `<root>/<id>` |

## System calls used and why
- `getrandom` – 6 random bytes → 12 hex characters of ID.
- `mkdir` (mode 0700) – creates the container directory; it is atomic, so it doubles as "claim this ID".
- `open`, `write` (via stdio), `fsync`, `rename` – atomic file replacement.
- `flock(LOCK_EX)` – two `minibox run` at once must not pick the same name, ID or subnet. The lock is released by closing the descriptor.
- `opendir`/`readdir`/`unlinkat`/`rmdir` – list and delete.
- `kill(pid, 0)` and `/proc/<pid>/{stat,status}` – is a process alive; is it a PID-namespace init.
- `if_nametoindex` – does the host veth still exist.

## OS concept
Processes share nothing but the filesystem and the kernel, so the filesystem is the "database" through which separate
commands find each other. Atomic `rename`, `mkdir` as an atomic claim and `flock` as a mutex between processes.
Zombie processes and PID reuse as the reasons "is this PID alive" needs care.

## Limitations
- The state is only as fresh as the monitor's last write. If the monitor is killed, `status` stays `running` and
  `dead` is deduced from the missing monitor.
- A process that is `kill -9`ed leaves no cleanup, so stale records exist until `minibox rm`.

## How to test
```sh
export MINIBOX_STATE_DIR=$(mktemp -d)           # no root needed for the read-only commands
printf 'id=aaaaaaaaaaaa\nname=x\nstatus=exited\nexit_code=3\ncommand=echo hi\n' \
  | { mkdir -m700 $MINIBOX_STATE_DIR/aaaaaaaaaaaa && cat > $MINIBOX_STATE_DIR/aaaaaaaaaaaa/state; }
./minibox ps -a ; ./minibox ps -a --json | python3 -m json.tool ; ./minibox rm x
sudo tests/phase4_test.sh                       # the full end-to-end test
```
