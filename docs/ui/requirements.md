# ui/requirements.txt

## Purpose
Pinned Python packages for the Flask web UI. `scripts/setup_ui.sh` (which `make ui-setup` prints) installs them into the virtual environment `ui/venv` so the UI does not depend on system Python packages. The file has no code, so there are no functions, system calls or errors of its own.

## Packages
Only Flask is imported by the UI (`ui/app.py`); the other six are its dependencies, pinned so that the versions are the same on every machine.

| Package | Version | Why it is needed |
|---------|---------|------------------|
| Flask | 3.1.3 | The web framework: routes, request/response objects, templates (`ui/app.py`). The only direct dependency. |
| Werkzeug | 3.1.9 | WSGI toolkit under Flask: request parsing, the development server, routing. |
| Jinja2 | 3.1.6 | Template engine for the HTML page in `ui/templates/`. |
| MarkupSafe | 3.0.4 | Escapes text inserted into templates (needed by Jinja2; prevents HTML injection). |
| itsdangerous | 2.2.0 | Signing helpers; a required dependency of Flask (the UI itself uses no sessions, it uses its own bearer token). |
| click | 8.5.0 | Command-line support of Flask (`flask` command); required dependency of Flask. |
| blinker | 1.9.0 | Signals (event hooks) inside Flask; required dependency of Flask. |

## Why exact versions
`==` pins make the lab reproducible and avoid a surprise upgrade of a package that runs a server which can start root processes.

## OS concept
None directly. It supports the UI, which only calls the minibox CLI (see `docs/ui/app.md` for the security model).

## How to test
- Run `scripts/setup_ui.sh`, then `ui/venv/bin/pip check` (reports no broken requirements).
- `python3 -c "import flask; print(flask.__version__)"` with `ui/venv/bin/python3` prints `3.1.3`.
