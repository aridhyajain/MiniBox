"""Wrapper around the minibox binary.

The UI never reimplements container logic: every action is one call to
`minibox <subcommand>`, started with an argument list (never through a shell)
and a timeout. Errors printed by the binary are mapped to typed exceptions so
the Flask layer can pick an HTTP status.
"""
import json
import os
import select
import subprocess
import threading
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The rootfs is chosen here, by the server. The browser can never supply a path.
ROOTFS = os.path.join(ROOT_DIR, "rootfs")

MAX_RUNNING = 10          # containers running at once, started from the UI
CMD_TIMEOUT = 15          # seconds for ps / run / rm / logs
STOP_GRACE = 3            # seconds `stop` waits after SIGTERM before SIGKILL
EXEC_LIMIT = 10           # seconds an exec'd command may run
EXEC_TIMEOUT = EXEC_LIMIT + 3
EXEC_GRACE = 2            # seconds between SIGTERM and SIGKILL of a stuck `minibox exec`
TIMEOUT_CODES = (124, 137, 143)   # busybox `timeout`, SIGKILL, minibox exec stopped by SIGTERM
MAX_OUTPUT = 64 * 1024    # bytes of command output / log returned to the browser
MAX_LOG_READ = 4 * 1024 * 1024   # bytes of a log we are willing to read

# Serialises "count running containers, then start one" so two requests
# cannot both slip under the limit.
_launch_lock = threading.Lock()


class MiniboxError(Exception):
    """The binary failed; str(error) is its message."""


class BinaryMissingError(MiniboxError):
    """The minibox binary or the rootfs is not where the server expects it."""


class NotFoundError(MiniboxError):
    """No container with that ID."""


class ConflictError(MiniboxError):
    """The request clashes with the container's state (name used, not running...)."""


class LimitError(ConflictError):
    """Too many running containers."""


def binary_path():
    return os.environ.get("MINIBOX_BIN") or os.path.join(ROOT_DIR, "minibox")


def _env():
    """A small explicit environment; MINIBOX_STATE_DIR lets tests use a temp state dir."""
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
    if os.environ.get("MINIBOX_STATE_DIR"):
        env["MINIBOX_STATE_DIR"] = os.environ["MINIBOX_STATE_DIR"]
    return env


def _check_installed(need_rootfs=False):
    if not os.access(binary_path(), os.X_OK):
        raise BinaryMissingError("minibox binary not found; run 'make' first")
    if need_rootfs and not os.path.isfile(os.path.join(ROOTFS, "bin", "busybox")):
        raise BinaryMissingError("Alpine rootfs missing; run 'make rootfs' first")


def _message(stderr):
    """First useful line of the binary's stderr, without the 'minibox: ' prefix."""
    text = stderr.decode("utf-8", "replace").strip()
    line = text.splitlines()[0] if text else "minibox failed"
    return line[len("minibox: "):] if line.startswith("minibox: ") else line


def _raise_for(stderr):
    """Turn the binary's error text into the matching exception."""
    message = _message(stderr)
    if "no such container" in message:
        raise NotFoundError(message)
    if "already used" in message or "not running" in message or "is running" in message:
        raise ConflictError(message)
    raise MiniboxError(message)


def _run(args, timeout=CMD_TIMEOUT):
    """Run `minibox args...` with an argument list, no shell, stdin closed, bounded time."""
    _check_installed()
    try:
        return subprocess.run([binary_path()] + args, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, env=_env(),
                              cwd=ROOT_DIR, check=False)
    except subprocess.TimeoutExpired:
        raise MiniboxError("minibox %s timed out after %d s" % (args[0], timeout))
    except OSError as err:
        raise MiniboxError("cannot run minibox: %s" % err.strerror)


def list_containers():
    """All containers (running, exited, dead) from `minibox ps -a --json`."""
    result = _run(["ps", "-a", "--json"])
    if result.returncode != 0:
        _raise_for(result.stderr)
    try:
        items = json.loads(result.stdout.decode("utf-8", "replace") or "[]")
    except ValueError:
        raise MiniboxError("minibox ps returned invalid JSON")
    return items if isinstance(items, list) else []


def run_detached(opts):
    """Start a detached container from validated options; returns its 12-hex ID."""
    _check_installed(need_rootfs=True)
    args = ["run", "-d", "--rootfs", ROOTFS, "--net", opts["net"]]
    for flag in ("name", "hostname", "memory", "cpus", "pids"):
        if opts.get(flag):
            args += ["--" + flag, opts[flag]]
    if opts.get("init") is True:         # a bare flag; only a real True turns it on
        args.append("--init")
    args += ["--"] + opts["argv"]
    with _launch_lock:
        running = sum(1 for c in list_containers() if c.get("status") == "running")
        if running >= MAX_RUNNING:
            raise LimitError("limit reached: %d containers are already running" % MAX_RUNNING)
        result = _run(args)
    if result.returncode != 0:
        _raise_for(result.stderr)
    container_id = result.stdout.decode("utf-8", "replace").strip()
    return container_id


def get_logs(container_id, tail):
    """Last `tail` lines of the container's log (stdout and stderr merged).

    `minibox logs` has no --tail, so we read its output ourselves, but never
    more than MAX_LOG_READ bytes: a runaway log must not fill the server's memory.
    """
    _check_installed()
    try:
        proc = subprocess.Popen([binary_path(), "logs", container_id],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=_env(), cwd=ROOT_DIR)
    except OSError as err:
        raise MiniboxError("cannot run minibox: %s" % err.strerror)
    timer = threading.Timer(CMD_TIMEOUT, proc.kill)
    timer.start()
    try:
        data = proc.stdout.read(MAX_LOG_READ + 1)
        truncated = len(data) > MAX_LOG_READ
        if truncated:
            proc.kill()
        stderr = proc.stderr.read(4096) if not truncated else b""
        proc.wait()
    finally:
        timer.cancel()
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0 and not truncated:
        _raise_for(stderr)
    lines = data[:MAX_LOG_READ].decode("utf-8", "replace").splitlines()
    text = "\n".join(lines[-tail:])
    if len(text) > MAX_OUTPUT:
        text = text[-MAX_OUTPUT:]
    return {"logs": text, "lines": min(len(lines), tail), "truncated": truncated}


def stop(container_id):
    """SIGTERM, then SIGKILL after STOP_GRACE seconds. Returns minibox's message."""
    result = _run(["stop", "--time", str(STOP_GRACE), container_id], timeout=STOP_GRACE + 12)
    if result.returncode != 0:
        _raise_for(result.stderr)
    return result.stdout.decode("utf-8", "replace").strip()


def remove(container_id):
    """Stop the container if it is running (SIGKILL at once), then remove it.

    `rm -f` alone would wait 5 s for a PID 1 that ignores SIGTERM, so we send
    `stop --time 0` first. A stop that fails for another reason (already dead)
    is fine: `rm -f` still cleans up. Only "no such container" is reported.
    """
    result = _run(["stop", "--time", "0", container_id])
    if result.returncode != 0 and "no such container" in _message(result.stderr):
        _raise_for(result.stderr)
    result = _run(["rm", "-f", container_id])
    if result.returncode != 0:
        _raise_for(result.stderr)


def exec_in(container_id, argv):
    """Run one command inside a running container and return its output.

    The command is wrapped with busybox `timeout -s KILL`, so it normally ends
    inside the container after EXEC_LIMIT seconds. If `minibox exec` is still
    running after EXEC_TIMEOUT, we stop it politely: SIGTERM (minibox then kills
    the command's whole process group), wait EXEC_GRACE seconds, then SIGKILL
    (the kernel kills the command itself, see PR_SET_PDEATHSIG in exec.c).
    Output read so far is returned. Returns {output, exit_code, timed_out, truncated}.
    """
    _check_installed()
    wrapped = ["timeout", "-s", "KILL", str(EXEC_LIMIT)] + argv
    started = time.monotonic()
    try:
        proc = subprocess.Popen([binary_path(), "exec", container_id, "--"] + wrapped,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=_env(), cwd=ROOT_DIR)
    except OSError as err:
        raise MiniboxError("cannot run minibox: %s" % err.strerror)
    try:
        chunks, eof = _read_until(proc, started + EXEC_TIMEOUT)
        stopped = not eof                      # deadline or output cap reached
        if stopped:
            _stop_client(proc)
            chunks += _drain(proc)
        else:
            try:
                proc.wait(timeout=EXEC_GRACE)
            except subprocess.TimeoutExpired:
                stopped = True
                _stop_client(proc)
    finally:
        proc.stdout.close()
    data = b"".join(chunks)
    truncated = len(data) > MAX_OUTPUT
    text = data[:MAX_OUTPUT].decode("utf-8", "replace")
    code = proc.returncode
    if code < 0:                               # killed by a signal: use the shell's 128+N
        code = 128 - code
    if code != 0 and ("no such container" in text or "not running" in text) \
            and text.startswith("minibox:"):
        _raise_for(text.encode())
    elapsed = time.monotonic() - started
    return {
        "output": text,
        "exit_code": code,
        "timed_out": (stopped and not truncated) or
                     (code in TIMEOUT_CODES and elapsed >= EXEC_LIMIT - 0.5),
        "truncated": truncated,
    }


def _read_until(proc, deadline):
    """Read the process's output until EOF, the deadline, or MAX_OUTPUT+1 bytes.

    Returns (chunks, eof). select() is used so a command that keeps the pipe
    open cannot block us past the deadline.
    """
    chunks = []
    total = 0
    fd = proc.stdout.fileno()
    while total <= MAX_OUTPUT:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return chunks, False
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            return chunks, True
        chunks.append(chunk)
        total += len(chunk)
    return chunks, False


def _stop_client(proc):
    """SIGTERM the `minibox exec` client, give it EXEC_GRACE seconds, then SIGKILL."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=EXEC_GRACE)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _drain(proc):
    """Collect output that is already in the pipe after the client was stopped."""
    chunks = []
    fd = proc.stdout.fileno()
    total = 0
    while total <= MAX_OUTPUT:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            break
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return chunks
