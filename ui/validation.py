"""Input validation for the minibox web UI.

Pure functions: no Flask, no subprocess. Every value that comes from the browser
passes through one of these before it can reach the minibox binary. Each function
returns a cleaned value or raises ValidationError(field, message).
"""
import math
import os
import re
import shlex

MAX_ARGS = 32          # arguments in one command
MAX_ARG_LEN = 256      # characters in one argument
MIN_MEMORY = 4 * 1024 * 1024
MAX_MEMORY = 16 * 1024 ** 3
MIN_CPUS = 0.1
MAX_PIDS = 4096
DEFAULT_TAIL = 200
MAX_TAIL = 1000

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,30}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
MEMORY_RE = re.compile(r"^([0-9]{1,12})([KMG]?)$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?$")
UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
LAUNCH_FIELDS = {"command", "name", "hostname", "memory", "cpus", "pids", "net"}


class ValidationError(ValueError):
    """A field failed validation; `field` names it, str(error) says why."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def _text(field, value):
    """Require a str without control characters (newline, NUL, ESC, ...)."""
    if not isinstance(value, str):
        raise ValidationError(field, "must be a string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValidationError(field, "must not contain control characters or newlines")
    return value


def _whole_number(field, value, low, high):
    """Accept a JSON integer or a string of digits; reject bool, float, junk."""
    if isinstance(value, bool):
        raise ValidationError(field, "must be a whole number")
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,10}", value):
        value = int(value)
    if not isinstance(value, int):
        raise ValidationError(field, "must be a whole number")
    if not low <= value <= high:
        raise ValidationError(field, "must be between %d and %d" % (low, high))
    return value


def validate_name(value):
    """Container name: lowercase letters, digits, _ . -, 1-31 chars. Empty = none."""
    if value is None or value == "":
        return ""
    value = _text("name", value)
    if not NAME_RE.fullmatch(value):
        raise ValidationError("name", "use 1-31 characters: a-z, 0-9, _ . - (start with a letter or digit)")
    if re.fullmatch(r"[0-9a-f]{12}", value):
        raise ValidationError("name", "must not look like a container ID (12 hex characters)")
    return value


def validate_hostname(value):
    """Hostname: letters, digits, '-' and '.', at most 63 characters. Empty = default."""
    if value is None or value == "":
        return ""
    value = _text("hostname", value)
    if not HOST_RE.fullmatch(value) or ".." in value:
        raise ValidationError("hostname", "use letters, digits, '-' and '.' (max 63, no leading or trailing '-' or '.')")
    return value


def validate_memory(value):
    """Memory limit like 64M, 512K, 1G (bytes if no suffix). Returns the canonical text."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValidationError("memory", "must look like 64M, 512K or 1G")
    text = str(value).upper()
    match = MEMORY_RE.fullmatch(text)
    if not match:
        raise ValidationError("memory", "must look like 64M, 512K or 1G")
    total = int(match.group(1)) * UNITS[match.group(2)]
    if not MIN_MEMORY <= total <= MAX_MEMORY:
        raise ValidationError("memory", "must be between 4M and 16G")
    return text


def validate_cpus(value, max_cpus=None):
    """CPU limit: a number from 0.1 up to the machine's core count. Returns text for --cpus."""
    if value is None or value == "":
        return ""
    if max_cpus is None:
        max_cpus = os.cpu_count() or 1
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValidationError("cpus", "must be a number")
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3})?", value):
            raise ValidationError("cpus", "must be a plain number like 0.5 or 2")
        value = float(value)
    if not math.isfinite(value) or not MIN_CPUS <= value <= max_cpus:
        raise ValidationError("cpus", "must be between %g and %d" % (MIN_CPUS, max_cpus))
    return "%g" % value


def validate_pids(value):
    """Process limit: 1-4096. Returns text for --pids, or "" if not given."""
    if value is None or value == "":
        return ""
    return str(_whole_number("pids", value, 1, MAX_PIDS))


def validate_net(value):
    """Network mode: 'none' (default) or 'veth'."""
    if value is None or value == "":
        return "none"
    if value not in ("none", "veth"):
        raise ValidationError("net", "must be 'none' or 'veth'")
    return value


def validate_command(value, field="command"):
    """Split a command line into argv with shlex (no shell ever sees it).

    '; rm -rf /' becomes the literal arguments ';' 'rm' '-rf' '/' - harmless,
    because the binary runs them with execvp, not through a shell.
    """
    value = _text(field, value)
    try:
        argv = shlex.split(value)
    except ValueError as err:
        raise ValidationError(field, "cannot parse: %s" % err)
    if not argv or not argv[0]:
        raise ValidationError(field, "must not be empty")
    if len(argv) > MAX_ARGS:
        raise ValidationError(field, "at most %d arguments" % MAX_ARGS)
    if any(len(arg) > MAX_ARG_LEN for arg in argv):
        raise ValidationError(field, "each argument is limited to %d characters" % MAX_ARG_LEN)
    return argv


def validate_container_id(value):
    """Container ID as printed by minibox: exactly 12 lowercase hex characters."""
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValidationError("id", "must be a 12-character container ID")
    return value


def validate_tail(value):
    """Number of log lines to return (1-1000, default 200); value may be None or a string."""
    if value is None or value == "":
        return DEFAULT_TAIL
    return _whole_number("tail", value, 1, MAX_TAIL)


def validate_launch(payload, max_cpus=None):
    """Validate the body of POST /api/containers. Returns a dict of cleaned fields."""
    if not isinstance(payload, dict):
        raise ValidationError("body", "must be a JSON object")
    unknown = sorted(set(payload) - LAUNCH_FIELDS)
    if unknown:
        raise ValidationError("body", "unknown field: %s" % ", ".join(unknown)[:80])
    if "command" not in payload:
        raise ValidationError("command", "is required")
    return {
        "argv": validate_command(payload["command"]),
        "name": validate_name(payload.get("name")),
        "hostname": validate_hostname(payload.get("hostname")),
        "memory": validate_memory(payload.get("memory")),
        "cpus": validate_cpus(payload.get("cpus"), max_cpus),
        "pids": validate_pids(payload.get("pids")),
        "net": validate_net(payload.get("net")),
    }


def validate_exec(payload):
    """Validate the body of POST /api/containers/<id>/exec. Returns the argv list."""
    if not isinstance(payload, dict):
        raise ValidationError("body", "must be a JSON object")
    unknown = sorted(set(payload) - {"command"})
    if unknown:
        raise ValidationError("body", "unknown field: %s" % ", ".join(unknown)[:80])
    if "command" not in payload:
        raise ValidationError("command", "is required")
    return validate_command(payload["command"])
