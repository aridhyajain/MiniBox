# scripts/build_deliverables.py

## Purpose
Builds the final deliverables **outside the repository**, in `~/minibox-deliverables/` (override with `--out DIR`; a directory inside the repo is refused). This first version produces the project documentation as a Word file, `MiniBox_Documentation.docx`, from the repo's existing Markdown (README, `docs/**/*.md`) and the three benchmark charts. Nothing is invented: every paragraph comes from those files. The Markdown is parsed once into a list of blocks (the document model), and the docx writer is the only part that knows about Word, so a PDF writer can reuse the same model later.

Standard library plus `python-docx` (docx) and `cairosvg` or `rsvg-convert` (charts). The packages live in a venv at `~/minibox-deliverables/venv`; the script never installs anything and never uses sudo; it prints the commands instead.

```sh
python3 scripts/build_deliverables.py check-tools   # tools found, routes chosen, commands for what is missing
python3 scripts/build_deliverables.py coverage      # every docs/**/*.md: included / excluded / missing
python3 scripts/build_deliverables.py docs          # build + verify the docx
make deliverables-check ; make deliverables-docs    # the same through make
```
Exit codes: 0 ok, 1 problem (missing doc or tool, failed verification), 2 refusal to write inside the repo.

## Output document (in this order)
Title page (from `config.json`) · table of contents (a real `TOC` field, pre-filled with the headings, marked dirty so Word refreshes the page numbers) · **1** Overview and how to build and run (README) · **2** User guide: CLI reference (README `Run`, `Manage containers`, `exec`; `docs/src/commands.md` usage/behaviour; an environment-variable table), Web UI guide with its security model (README, `docs/ui/app.md`) · **3** Architecture · **4** Program documentation, one section per file grouped `include/ src/ scripts/ tests/ ui/ bench/` plus the build system (`docs/Makefile.md`) · **5** Testing and benchmarks (test overview table, `docs/BENCHMARKS.md` with the three charts as PNG) · **6** Limitations (README) · Appendix A/B: `docs/REVIEW1.md`, `docs/REVIEW2.md` · Appendix C only if a doc exists that fits no other place. Excluded (listed by `coverage` with the reason): `docs/VIVA_NOTES.md`, `docs/DEVLOG.md` (personal notes), `docs/scripts/build_deliverables.md`, `docs/tests/test_build_deliverables.md` and the README section "Deliverables" (they describe the deliverable tooling itself, not the project). A `(from <file>)` suffix on a heading is removed. Headings are numbered by the script (`1`, `1.1`, `Appendix A`, `A.1`); the source's own leading numbers are removed. Header: project title. Footer: page number field (not on the title page). Code blocks: Courier New on a light grey background. Tables: borders, 9 pt text (inline code 9.5 pt), shaded bold header row repeated on every page, fixed layout with column widths from the content (below), rows may break across pages so tall rows leave no white gaps.

`config.json` (created if missing, never overwritten) has `student_name` (default "Aridhya Jain"), `register_no`, `course`, `faculty_name`, `institute`, `team_number`, `project_title` (default "MiniBox: A Mini Container Runtime") and `date`. Empty values appear on the title page as highlighted placeholders such as `[REGISTER NUMBER]`, and the build prints a warning listing them (once). The exception is `date`: if it is missing or empty, today's date is used, written like `19 October 2026`, without a warning; if it is set, it is used as written.

**Table column widths.** `compute_col_widths` starts each column proportional to its longest cell text, clamps it between 8% and 70% of the text width (9298 twips = 16.4 cm), never makes it narrower than its longest unbreakable token (a file path) where that fits (the floor is capped at the maximum and scaled down if all floors together exceed the page), redistributes the difference over the other columns (water-filling), and rounds so the widths add up to exactly the text width. They are written as `w:tblLayout fixed`, `w:tblW` in twips, `w:gridCol` and `w:tcW`.

**Build checks.** Besides leftovers, doc presence, numbering and the TOC, the build fails (exit 1) listing the problems if: a table is not fixed-layout, its widths do not add up to the text width, its header is not repeated or a row cannot break; a heading still has a `(from ...)` suffix; an excluded README section appears; or the "Test suite overview" table lacks a row for any `tests/*.sh` or `tests/test_*.py` (whose doc is not excluded), the missing names being printed. The summary prints the data-row count of the five largest tables.

## Functions
| Signature | Inputs | Returns | Errors / side effects |
|-----------|--------|---------|-----------------------|
| `assert_outside_repo(path, repo=REPO)` | a path | the resolved `Path` | `RepoWriteError` if the path (after resolving symlinks) is the repo or inside it. Called before every write. |
| `ensure_config(out_dir, repo)` | output folder | path of `config.json` | creates the folder and the template only if missing; never overwrites |
| `load_config(out_dir, repo)` | output folder | dict of strings (defaults fill missing keys) | `BuildError` on invalid JSON |
| `config_values(cfg, today=None)` | config dict, optional date | `(values with placeholders, list of empty keys)` | none (pure). An empty/missing `date` becomes `today_text()` and is not listed as empty. |
| `today_text(today=None)` | optional `datetime.date` | `"19 October 2026"` | none (pure) |
| `probe_modules(out_dir, names)` | folder, module names | `{name: 'system' / 'venv' / None}` | runs the venv's python once to look |
| `choose_routes(tools, modules)` | found tools and modules | `{markdown_to_docx, svg_to_png, pdf}` | none (pure). SVG: `rsvg-convert`, else `cairosvg`. PDF (later): LibreOffice, weasyprint, wkhtmltopdf, chromium, reportlab. |
| `cmd_check_tools(args)` | CLI args | 0 if docx and SVG routes exist, else 1 | prints the report and the exact venv/pip/`sudo apt install` commands (never runs them) |
| `all_docs(repo)`, `expected_docs(repo)`, `fixed_docs()`, `group_docs(docs, group)` | repo | sorted lists of doc paths | `expected_docs` is the doc each source file must have (`src/x.c` -> `docs/src/x.md`, ...) |
| `classify(repo)` | repo | `{path: (status, where)}` with status included / excluded / missing; for excluded, `where` is the reason | none. `EXCLUDED` (dict path -> reason) and `EXCLUDED_README_SECTIONS` give the exclusions; the README section appears as `README.md (section 'Deliverables')`. A doc that belongs to no section is still included (Appendix C). |
| `cmd_coverage(args)` | CLI args | 1 if anything is missing | prints one line per doc and the totals |
| `parse_inline(text, ...)`, `runs_text(runs)`, `clean_heading(text)`, `strip_provenance(text)` | Markdown text | runs `(text, {b, i, code})`, plain text, heading without its `(from <file>)` suffix | none. Handles code spans (single and double backtick), bold, italic, links, escapes. |
| `split_row(line)` | one table row | list of cells | keeps `\|` and pipes inside code spans |
| `parse_markdown(text, base_dir, shift, drop_title)` | Markdown | list of blocks `h p li code table image quote` | none. Images are resolved against `base_dir`. Fenced code is kept verbatim. |
| `md_sections(text)`, `find_section(secs, prefix)`, `drop_excluded_sections(secs)`, `first_paragraph_of(text)` | Markdown | pieces of a doc by `##` heading | `BuildError` if a required section is missing |
| `Model`, `build_model(repo)` | repo | the document in the order above, with numbers | `BuildError` if a doc is missing |
| `normalize_levels(blocks)`, `number_headings(blocks)`, `resolve_xrefs(blocks)` | blocks | numbered headings; the test overview table gets each doc's section number | none |
| `find_leftovers(line)` | one line of non-code output | list of problems (`**`, `\|---\|`, fences, backticks, `#`, list markers, raw HTML, link/image syntax) | none (pure) |
| `check_heading_numbers(headings)` | `[(level, text)]` | list of problems | none (pure). Checks levels, sibling order, no duplicated numbers. |
| `flatten_svg(svg)`, `svg_to_png(svg, png, route)` | chart SVG | PNG file | the charts colour everything with CSS variables and a dark-mode media query; the variables are replaced by their light values first so any converter draws them correctly |
| `compute_col_widths(rows, total, min_pct, max_pct)` | rows of plain-text cells | list of widths in twips adding up to `total` | none (pure); see "Table column widths" |
| `build_docx(model, values, out_dir, svg_route)` | model | `(path, stats)` | writes `MiniBox_Documentation.docx` and PNGs in `build/` under the output folder |
| `docx_text_parts(path)`, `docx_tables(path)` | the built file | headings and text; every table with its heading and cell text, read back | none |
| `expected_test_rows(repo)`, `missing_test_rows(table_rows, expected)`, `largest_tables(tables, count)`, `check_table_layout(path)` | repo / table rows / file | names a test overview should have; the missing ones; the biggest tables; layout problems | none (read-only) |
| `verify_docx(path, model, repo)` | the built file | number of headings | `BuildError` listing the problems: leftovers, missing included doc (its title must be a heading), bad numbering, no TOC field, bad table layout, provenance suffix, excluded section present, missing test overview rows |
| `render_pdf_check(docx, build_dir)` | docx | page count, or `None` without LibreOffice | runs `soffice --headless --convert-to pdf` into the build folder |
| `cmd_docs(args)` | CLI args | 0 | re-runs itself with the venv's python first if `python-docx` is only there (so the config note and warning print once), then config warning, build, verify, report incl. the five largest tables |
| `main(argv)` | argv | exit code | 2 for a repo-path refusal, 1 for `BuildError` |

## System calls and library calls used and why
- `stat`/`open`/`read`/`write`/`mkdir` (through `pathlib`): reading the repo's Markdown, writing the docx, PNGs, `config.json`.
- `realpath` (`Path.resolve`): resolves `..` and symlinks so a path that points into the repo is recognised and refused.
- `fork`/`execve`/`wait` (through `subprocess`): `rsvg-convert`, `soffice`, and the venv's python for module probing.
- `execv`: replaces the process with the venv's python when `python-docx` is only installed there (done before any output, otherwise every message would be printed by both processes).
- `which` (`shutil.which`, a `PATH` search with `stat`): tool detection.

## OS concept
Files and paths (path resolution, symlinks, the repo boundary as a safety rule), processes (running external tools, `exec`), isolated Python environments (venv) instead of system-wide installs, and exit codes as an interface.

## How to test
```sh
python3 -m unittest tests/test_build_deliverables.py -v     # no root, temp dirs only
python3 scripts/build_deliverables.py check-tools
python3 scripts/build_deliverables.py coverage              # must show missing: 0
python3 scripts/build_deliverables.py docs                  # prints "verification: OK"
python3 scripts/build_deliverables.py --out . docs ; echo $?   # run in the repo: refused, exit 2
```
Open `~/minibox-deliverables/MiniBox_Documentation.docx` in Word or LibreOffice; update the table of contents (right-click, Update field) to fill in page numbers.
