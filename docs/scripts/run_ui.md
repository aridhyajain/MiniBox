# scripts/run_ui.sh

## Purpose
Starts the web UI on `127.0.0.1:<port>` (default 8080) with the venv's Python. It must run as root because `minibox` itself needs root
(namespaces, mounts, cgroups); the script refuses otherwise. The server prints the access URL (with a fresh random token) once at startup.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `die msg` | message | exits 1 | prints `error: msg` to stderr |

Main flow: require root -> check the port is a number -> check `ui/venv/bin/python`, `./minibox` and `rootfs/bin/busybox` exist (with the
command that fixes each) -> `exec ui/venv/bin/python ui/app.py --host 127.0.0.1 --port <port>`. The host is fixed to 127.0.0.1 in the script;
`app.py` also refuses any non-loopback `--host` if it is run by hand.

## System calls / commands used and why
`id -u` (`geteuid`); `exec` (`execve`) replaces the shell with Python so Ctrl-C and signals go straight to the server.

## OS concept
Privilege separation: setup is done as a normal user, only the final launch needs root, and the listening socket is limited to the
loopback interface.

## How to test
```sh
scripts/run_ui.sh                 # as a normal user: refuses (exit 1)
sudo scripts/run_ui.sh 8080       # prints http://127.0.0.1:8080/?token=...
ss -ltn | grep 8080               # shows 127.0.0.1:8080 only
```
