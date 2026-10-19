# scripts/setup_ui.sh

## Purpose
Creates the Python virtual environment for the web UI in `ui/venv` and installs the pinned packages from
`ui/requirements.txt` (Flask 3.1.3 and its dependencies). Runs as your normal user; no sudo. `ui/venv/` is git-ignored.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `die msg` | message | exits 1 | prints `error: msg` to stderr |

Main flow: refuse to run as root (the venv would be owned by root) -> check `python3` and `ui/requirements.txt` -> create `ui/venv` with
`python3 -m venv` if missing (hint: `sudo apt install python3-venv`) -> `pip install -r ui/requirements.txt` -> import Flask to check it worked.
Safe to run again. Needs internet access for `pip`.

## System calls / commands used and why
`id -u` (`geteuid`) to detect root; `python3 -m venv` (creates directories, copies/symlinks the interpreter); `pip install` (network,
file writes inside `ui/venv`).

## OS concept
Per-project dependency isolation: a venv is a directory with its own interpreter link and `site-packages`, so the UI's libraries never touch
the system Python. Running the UI as root later reuses this venv; the venv is only read.

## How to test
```sh
scripts/setup_ui.sh
ui/venv/bin/python -c "import flask; print(flask.__version__)"   # 3.1.3
sudo scripts/setup_ui.sh                                          # refuses (exit 1)
```
