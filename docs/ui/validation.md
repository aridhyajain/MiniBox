# ui/validation.py

## Purpose
Strict, pure input validation for the web UI. Every value that arrives from the browser (JSON body fields, URL path
parts, query parameters) passes through one of these functions before it can reach the `minibox` binary. The module
imports only `math`, `os`, `re` and `shlex`: no Flask, no `subprocess`, so it can be unit-tested without root.
Every function returns a cleaned value or raises `ValidationError(field, message)`; the Flask layer turns that into
HTTP 400 `{"error": {"code": "invalid_input", "field": ..., "message": ...}}`.

Rules shared by all text fields: values must be `str` (or the stated number type), must contain no control
characters (newline, tab, NUL, ESC), and are matched with `fullmatch` (so a trailing `\n` cannot slip past `$`).
Empty or missing optional fields mean "not given" and return `""` (or the default).

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `ValidationError(field, message)` | field name, text | exception (subclass of `ValueError`) with `.field` | – |
| `validate_name(value)` | container name or empty | the name, or `""` | `^[a-z0-9][a-z0-9_.-]{0,30}$` only; rejects a 12-hex string (the CLI reserves those for IDs) |
| `validate_hostname(value)` | hostname or empty | the hostname, or `""` | letters, digits, `-` and `.`; max 63; no leading/trailing `-`/`.`; no `..` |
| `validate_memory(value)` | `64M`, `512K`, `1G`, or plain bytes (str/int) or empty | canonical upper-case text | `<digits>[K\|M\|G]`; total must be 4M to 16G; rejects negatives, decimals, `1e9`, bool |
| `validate_cpus(value, max_cpus=None)` | number or numeric str (`0.5`) or empty | text for `--cpus` (e.g. `"0.5"`) | 0.1 up to `os.cpu_count()`; rejects nan, inf, exponents, bool |
| `validate_pids(value)` | int or digit str or empty | text for `--pids` | 1 to 4096; rejects floats, bool, negatives, huge numbers |
| `validate_net(value)` | `none`/`veth`/empty | `"none"` or `"veth"` | anything else is rejected |
| `validate_command(value, field)` | command line string | list of arguments | `shlex.split`; rejects control chars, unbalanced quotes, empty command or empty first argument, more than 32 arguments, any argument over 256 characters |
| `validate_container_id(value)` | URL path part | the ID | exactly 12 lowercase hex characters (blocks `../`, `-rf`, `$(id)` in paths) |
| `validate_tail(value)` | query string value or `None` | int 1 to 1000 (default 200) | digits only |
| `validate_init(value)` | decoded JSON value | the same `bool` | **strict**: only `true` / `false` pass; strings (`"true"`), numbers (`1`), `null`, lists and dicts raise `ValidationError("init", "must be true or false")` |
| `validate_launch(payload, max_cpus=None)` | decoded JSON body | dict `argv, name, hostname, memory, cpus, pids, net, init` (`init` is `False` when absent) | body must be an object; unknown fields (e.g. `rootfs`) are rejected; `command` is required |
| `validate_exec(payload)` | decoded JSON body | argv list | object with only `command` |

Internal helpers: `_text(field, value)` (str without control characters), `_whole_number(field, value, low, high)`.

## System calls used and why
None. The module is deliberately free of I/O (only `os.cpu_count()`, which reads the number of online CPUs).

## OS concept
Trust boundaries. The UI process runs as root and starts processes, so everything from the network is untrusted
input and is checked against a whitelist (allowed shape) rather than a blacklist (bad characters). The command is
split with `shlex` into an argument list and later passed to `execvp` without a shell, so `; rm -rf /` or `$(id)`
are just literal arguments, never interpreted.

## How to test
```sh
python3 -m unittest tests/test_ui_validation.py -v      # no root needed
```
