# scripts/make_report_diagrams.py

## Purpose
Writes the five diagrams of the project report as SVG files and copies its three benchmark charts, **outside the repository**, to `~/minibox-deliverables/report/figures/` (override with `--out DIR`; a directory inside the repo is refused, exit 2). Standard library only. The PDF build (`scripts/build_deliverables.py report`) embeds the files directly.

| File | Shows | Source of the facts |
|------|-------|---------------------|
| `architecture.svg` | web UI, `minibox` CLI, monitor process, container init (and the optional `--init` variant), state directory, namespaces, cgroup and veth pair; `exec` as a dashed arrow | `docs/ARCHITECTURE.md`, `src/monitor.c`, `src/container.c` |
| `startup_sequence.svg` | parent, kernel and container process: preparation, `mkdir` of the cgroup and limits, `clone3(... CLONE_INTO_CGROUP)`, child set-up, the `R`/`G`/`A` handshake, veth set-up, `on_started`, `execvp` or `minit_run`, `waitpid`, clean-up, and the `clone()` fallback as a note | `src/container.c` (`container_run`, `launch`, `supervise`, `child_setup`), `src/rootfs.c`, `src/net.c`, `src/cgroup.c` |
| `cgroup_hierarchy.svg` | `/sys/fs/cgroup` -> `minibox/` -> `box-<id>/` with the limit files, the statistic files and `cgroup.procs` | `src/cgroup.c`, `docs/src/cgroup.md` |
| `ui_request_path.svg` | the checks a web-interface request meets, in the order of the code: loopback binding, `Host` header (400), token with `hmac.compare_digest` (401), JSON body and 16 KB cap (400, 413), validation (400), at most 10 running containers (409), `minibox_client.py` with an argument list and no shell, then the `minibox` command | `ui/app.py` (`check_host_and_token`, `json_body`, routes, `MAX_BODY`), `ui/minibox_client.py` (`run_detached`, `MAX_RUNNING`), `docs/ui/*.md` |
| `lifecycle.svg` | the states `registered`, `running`, `exited`, `removed` and the derived `dead`, and the escalation of `stop` (SIGTERM, poll every 100 ms for `--time` seconds, SIGKILL, wait up to 5 s) | `src/commands.c` (`stop_container`, `STOP_DEFAULT_SECONDS`, `KILL_WAIT_SECONDS`, `POLL_NS`), `src/state.c`, `src/monitor.c` |

The charts `startup.svg`, `memory.svg` and `limits.svg` are copied from `bench/results/reference/` (made by `bench/make_charts.py`) with `flatten_svg` of `build_deliverables.py`: the CSS variables are resolved to the light theme and the dark-mode block is dropped, because WeasyPrint does not evaluate them (unflattened, the chart background renders black). Nothing else in them is changed. The report build prints them on landscape pages so that their text is legible.

Design rules: all diagrams are 620 units wide so that a figure printed at 16.4 cm shows 12-unit text at about 9 pt; only grays (black, dark gray, light gray, white) are used and optional or indirect things are dashed, so the figures print in black and white; arrowheads are drawn as explicit polygons (not SVG markers, which some renderers draw pointing the wrong way); every text is escaped and checked against the width it has to fit (a text that does not fit is an error, not a silent overflow).

```sh
python3 scripts/make_report_diagrams.py              # writes the five diagrams and copies the three charts
python3 scripts/make_report_diagrams.py --out DIR    # elsewhere (not inside the repo)
make deliverables-report                             # diagrams, then the PDF
```
Exit codes: 0 ok, 1 a text does not fit its box, 2 output path inside the repository.

## Functions
| Signature | Inputs | Returns | Errors / side effects |
|-----------|--------|---------|-----------------------|
| `text_width(s, size=12, mono=False)` | a text | upper-bound width in user units | none (pure) |
| `class Svg(height, title, desc)` | canvas height, accessible title and description | a builder collecting elements | none |
| `Svg.text(x, y, s, size, anchor, bold, mono, fill, fit)` | position and text | nothing (adds a `<text>`) | `ValueError` if the text is wider than `fit` (default: the canvas) |
| `Svg.lines(x, y, lines, anchor, bold_first, fit, mono)` | several lines, 15 units apart | nothing | as `text` |
| `Svg.box(x, y, w, h, lines, fill, dash, bold_first, rx, mono)` | a rectangle with centred text (first line bold) | nothing | `ValueError` if a line does not fit `w - 8` |
| `Svg.line(pts, dash, arrow, width)` / `Svg.arrow(pts, dash)` | polyline points | nothing; with an arrow, a filled polygon ends the line and the line stops at the head's base | none |
| `Svg.label_bg(cx, y_first, lines)` | label position | nothing (a white patch so lines do not run through letters) | none |
| `Svg.render()` | - | the SVG document as a string | none |
| `architecture()`, `startup_sequence()`, `cgroup_hierarchy()`, `lifecycle()`, `ui_request_path()` | - | the SVG text of one figure | `ValueError` from the fit check |
| `write_all(out_dir, chart_source)` | output folder, folder of the charts (default `bench/results/reference`) | list of the eight paths (`FIGURES` then `CHARTS`) | `RepoWriteError` if `out_dir` (or a target, after symlink resolution) is inside the repo; `ValueError` if a chart file is missing; creates the folder |
| `main(argv)` | argv | exit code | 2 for a repo path, 1 for a text that does not fit |

`startup_sequence()` lays the diagram out from a list of items (`band`, `note`, `msg`), each of which advances a vertical cursor, so adding a step only means adding one item.

## System calls used and why
`mkdir`, `open`, `read`, `write` (through `pathlib`): creating the output folder, reading the three charts and writing the SVG files. `realpath` (`Path.resolve`, through `assert_outside_repo` imported from `build_deliverables.py`): recognise and refuse an output path that points into the repository, also through a symlink. No process is started.

## OS concept
The diagrams draw the concepts the project implements: processes and their parent/child relationship, system calls crossing from user space into the kernel (`clone3`, `pivot_root`, `waitpid`), pipes as synchronisation and failure detection, the cgroup v2 file hierarchy and its no-internal-process rule, signals and the special treatment of PID 1. The script itself demonstrates only the path-resolution safety rule shared with `build_deliverables.py`.

## How to test
```sh
python3 -m unittest tests/test_build_deliverables.py -v    # class DiagramTests
python3 scripts/make_report_diagrams.py                    # eight "wrote ..." lines
python3 scripts/make_report_diagrams.py --out . ; echo $?  # run in the repo: refused, exit 2
```
Look at the files in a browser or let `make deliverables-report` embed them: they must be readable in black and white.
