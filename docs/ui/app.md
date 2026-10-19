# ui/app.py

## Purpose
The Flask application of the web UI: routes only, plus the security checks in front of them. It never touches
containers directly; every route validates its input (`validation.py`) and calls `minibox_client.py`.

## Security model (read this first)
The server can start and control **root** processes, so it is locked down:
- **Loopback only.** `main()` refuses any `--host` that is not `localhost` or an IPv4 `127.x.x.x` address and binds `127.0.0.1`.
  The port is configurable (`--port`, default 8080). From Windows, WSL2 forwards `127.0.0.1:<port>` to the Linux side, so a normal
  Windows browser can open it without exposing the server to the network.
- **Random token.** `secrets.token_urlsafe(32)` is generated at startup and printed once as
  `http://127.0.0.1:<port>/?token=<token>`. Every `/api` request needs `Authorization: Bearer <token>`, compared with
  `hmac.compare_digest` (constant time). The page reads the token from the URL, keeps it in `sessionStorage`, removes it from the
  address bar and sends it as a header. (`MINIBOX_UI_TOKEN` can set a known token; only the test script uses it.)
- **Host header check.** Requests whose `Host` is not `127.0.0.1:<port>` or `localhost:<port>` get 400. This stops DNS rebinding,
  where a hostile web page makes the browser talk to 127.0.0.1 under another name. No CORS headers are ever sent, so other
  web sites cannot read responses. The token in a header (not a cookie) also rules out CSRF.
- **Strict validation** of every field, a 16 KB request-body cap (413), at most 10 running containers (409), a fixed rootfs
  chosen by the server, `Content-Type: application/json` required for bodies.
- **No shell.** All process calls use argument lists (see `minibox_client.py`).
- Response headers: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` (the token in the first URL is never leaked as a referrer), and a Content-Security-Policy
  `default-src 'self'` (the page loads nothing external and has no inline scripts). The request log hides `token=...`.

**Limits.** A minibox container is **not a hard security boundary**: it shares the host kernel, runs as root (no user
namespace), has no seccomp filter, no capability dropping and no read-only paths. Anyone who can use this UI can run commands as
root on the host, and a process in a container may be able to escape. That is why the UI must stay on localhost, must never be put
behind a port forward or proxy, and why the URL with the token must be kept private. The token protects against other web pages and
other local users who do not know it; it does not protect against someone who already controls the machine.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `error_response(code, message, status, field=None)` | – | JSON `{"error": {"code", "message", "field"?}}` and status | the one error shape used everywhere |
| `create_app(token, port, bind_host)` | token, port, bind address | Flask app | registers the hooks and routes below |
| `check_host_and_token()` (before every request) | request | `None` or an error response | 400 `bad_host`; 401 `unauthorized` (for `/api/*`) |
| `security_headers(resp)` (after every request) | response | response | adds the headers above |
| `handle_error(err)` | any exception | error response | maps `ValidationError` to 400, `NotFoundError` 404, `ConflictError`/`LimitError` 409, `BinaryMissingError` 503, `MiniboxError` 500; HTTP errors (404, 405, 413, 415) keep their status; unexpected errors give a generic 500 and are logged |
| `json_body()` | request | decoded JSON | `ValidationError` unless the body is JSON |
| `index()` `GET /` | – | the HTML page | needs the Host check only (the page must load to read the token) |
| `list_containers()` `GET /api/containers` | – | `{"containers": [...]}` | from `ps -a --json` |
| `launch()` `POST /api/containers` | `command`, optional `name`, `hostname`, `memory`, `cpus`, `pids`, `net` | 201 `{"id"}` | 400, 409 (name used / limit reached), 413, 500 |
| `logs(cid)` `GET /api/containers/<id>/logs?tail=N` | ID, tail 1-1000 (default 200) | `{"id", "logs", "lines", "truncated"}` | 400, 404 |
| `stop(cid)` `POST /api/containers/<id>/stop` | ID | `{"id", "message"}` | 400, 404 |
| `remove(cid)` `DELETE /api/containers/<id>` | ID | `{"id", "removed": true}` | 400, 404 |
| `exec_command(cid)` `POST /api/containers/<id>/exec` | `command` | `{"id", "output", "exit_code", "timed_out", "truncated"}` | 400, 404, 409 (not running); 10 s limit |
| `_RedactToken` (log filter) | log record | record | replaces `token=...` with `token=<hidden>` |
| `check_loopback(host)` | `--host` value | address to bind | exits with a message for non-loopback hosts |
| `main()` | `--host`, `--port` | – | exits on a bad host/port/token; warns if not root; prints the URL once; runs the threaded server (never in debug mode) |

## System calls used and why
`socket`/`bind`/`listen`/`accept` (through Werkzeug) on `127.0.0.1`; `getrandom` (via `secrets`) for the token; `geteuid` to warn when
not root. Container work is delegated to `minibox_client.py`.

## OS concept
The network attack surface of a privileged service: binding to the loopback interface, authentication, why the Host header and
the same-origin policy matter for services on localhost, and why containers built from namespaces and cgroups alone are isolation
but not a security boundary.

## How to test
```sh
scripts/setup_ui.sh                  # once, no sudo
sudo tests/phase5_test.sh            # full HTTP checks (401, bad Host, ss, validation, launch...exec...cleanup)
python3 -m unittest tests/test_ui_validation.py -v
```
Without root you can still start `ui/venv/bin/python ui/app.py --port 8099` and use `curl` to check 401 / bad Host / 400 / 413;
container actions then fail with a permission error because the state directory belongs to root.
