# ui/minibox_client.py

## Purpose
The only place where the web UI runs the `minibox` binary. The UI does not reimplement any container logic: each
action is one CLI call (`ps -a --json`, `run -d`, `logs`, `stop`, `rm`, `exec`), started with an argument list (never
`shell=True`), `stdin` closed, a small explicit environment, and a timeout. Text the binary prints on stderr is mapped
to typed exceptions so the Flask layer can pick an HTTP status.

Server-side choices the browser cannot influence: the binary (`<repo>/minibox`, or `MINIBOX_BIN`), the root
filesystem (`<repo>/rootfs`, passed as an absolute `--rootfs`), the working directory, the environment
(`PATH` and, if set, `MINIBOX_STATE_DIR`), and all timeouts and output caps.

Constants: `MAX_RUNNING=10` containers, `CMD_TIMEOUT=15` s, `STOP_GRACE=3` s, `EXEC_LIMIT=10` s (`EXEC_TIMEOUT` = limit + 3 s),
`MAX_OUTPUT=64 KB` returned, `MAX_LOG_READ=4 MB` read from a log.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `binary_path()` | – | path of the binary | – |
| `_env()` | – | dict for the child | – |
| `_check_installed(need_rootfs)` | flag | – | `BinaryMissingError` if `./minibox` is not executable or `rootfs/bin/busybox` is missing |
| `_message(stderr)` | bytes | first stderr line without the `minibox: ` prefix | – |
| `_raise_for(stderr)` | bytes | never returns | `NotFoundError` ("no such container"), `ConflictError` ("already used", "not running", "is running"), else `MiniboxError` |
| `_run(args, timeout)` | argument list | `CompletedProcess` | `MiniboxError` on timeout or OS error; runs the binary |
| `list_containers()` | – | list of dicts from `ps -a --json` | `MiniboxError` if the command fails or prints invalid JSON |
| `run_detached(opts)` | validated options from `validation.validate_launch` | the new 12-hex ID | `LimitError` if 10 containers already run; `ConflictError` for a used name; `MiniboxError` otherwise. A lock makes "count running, then start" atomic. Starts a container; when `opts["init"] is True` the bare flag `--init` is added (a flag, never a value from the browser) |
| `get_logs(id, tail)` | ID, line count | `{logs, lines, truncated}` | reads at most 4 MB of `minibox logs` output (`Popen`, killed after 15 s); `NotFoundError`, `MiniboxError`. `minibox logs` has no `--tail`, so the last lines are cut here |
| `stop(id)` | ID | the binary's message | `stop --time 3`: SIGTERM, 3 s, then SIGKILL. `NotFoundError`, `MiniboxError` |
| `remove(id)` | ID | – | `stop --time 0` (so a PID 1 that ignores SIGTERM is killed at once), then `rm -f`. `NotFoundError`, `MiniboxError` |
| `exec_in(id, argv)` | ID, argument list | `{output, exit_code, timed_out, truncated}` | runs `minibox exec <id> -- timeout -s KILL 10 <argv>`; stdout and stderr merged, 64 KB cap; `NotFoundError`/`ConflictError` if the container is unknown or not running. A negative return code (killed by a signal) is reported as 128+N |
| `_read_until(proc, deadline)` | `Popen`, time | `(chunks, eof)` | reads with `select` so a command that keeps the pipe open cannot block past the deadline; stops at the output cap |
| `_stop_client(proc)` | `Popen` | – | SIGTERM, wait `EXEC_GRACE` (2 s), then SIGKILL |
| `_drain(proc)` | `Popen` | chunks | output already in the pipe after the client was stopped ("output so far") |

Time limits for `exec`: busybox `timeout -s KILL 10` inside the container is the normal way a long command ends (exit 124 or 137,
`timed_out` true). If `minibox exec` is still running after `EXEC_TIMEOUT` (13 s) or the output cap is reached, the client is stopped
politely: SIGTERM (`minibox exec` then SIGKILLs the command's whole process group and exits 143), 2 s of grace, then SIGKILL (the kernel
kills the command's direct child through `PR_SET_PDEATHSIG`; see `docs/src/exec.md`). Output read so far is returned. `timed_out` is true
when the client had to be stopped, or the exit code is 124/137/143 and the command ran for about 10 s. There is no `shell=True` and no
process-group kill from Python: the group handling lives in `minibox exec`.

## System calls used and why
Through `subprocess`: `fork`/`execve` to start the binary; `pipe` for its output; `select`/`read` with a deadline; `kill(SIGTERM)` then
`kill(SIGKILL)` and `wait4` for the exec client. `os.access(X_OK)` to find the binary.

## OS concept
Process boundaries and least privilege in the wrapper: the root-capable binary is invoked with a fixed argument vector (no shell
parsing, so no injection), a clean environment, closed stdin, bounded time and bounded output. Also: why killing a client does
not kill the process it started inside another PID namespace.

## How to test
Needs root and a built `minibox` and rootfs; it is covered end to end by:
```sh
sudo tests/phase5_test.sh
```
Without root, `list_containers()` raises `MiniboxError` ("cannot read ... Permission denied").
