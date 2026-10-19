# scripts/build_deliverables.py

## Purpose
Builds the final deliverables **outside the repository**, in `~/minibox-deliverables/` (override with `--out DIR`; a directory inside the repo is refused). It produces the project documentation as a Word file, `MiniBox_Documentation.docx`, from the repo's existing Markdown (README, `docs/**/*.md`) and the three benchmark charts, and the whole project report as `MiniBox_Report.pdf` (subcommand `report`, below). Nothing is invented: every paragraph comes from those files. The Markdown is parsed once into a list of blocks (the document model), and the docx writer is the only part that knows about Word, so a PDF writer can reuse the same model later.

Standard library plus `python-docx` (docx), `cairosvg` or `rsvg-convert` (charts), and `weasyprint` with `pypdfium2` (PDF report). The packages live in a venv at `~/minibox-deliverables/venv`; the script never installs anything and never uses sudo; it prints the commands instead.

```sh
python3 scripts/build_deliverables.py check-tools   # tools found, routes chosen, commands for what is missing
python3 scripts/build_deliverables.py coverage      # every docs/**/*.md: included / excluded / missing
python3 scripts/build_deliverables.py docs          # build + verify the docx
python3 scripts/build_deliverables.py report        # build + verify the PDF report (needs the diagrams, see below)
make deliverables-check ; make deliverables-docs ; make deliverables-report    # the same through make
```
Exit codes: 0 ok, 1 problem (missing doc or tool, failed verification), 2 refusal to write inside the repo.

## Output document (in this order)
Title page (from `config.json`) · table of contents (a real `TOC` field, pre-filled with the headings, marked dirty so Word refreshes the page numbers) · **1** Overview and how to build and run (README) · **2** User guide: CLI reference (README `Run`, `Manage containers`, `exec`; `docs/src/commands.md` usage/behaviour; an environment-variable table), Web UI guide with its security model (README, `docs/ui/app.md`) · **3** Architecture · **4** Program documentation, one section per file grouped `include/ src/ scripts/ tests/ ui/ bench/` plus the build system (`docs/Makefile.md`) · **5** Testing and benchmarks (test overview table, `docs/BENCHMARKS.md` with the three charts as PNG) · **6** Limitations (README) · Appendix A/B: `docs/REVIEW1.md`, `docs/REVIEW2.md` · Appendix C only if a doc exists that fits no other place. Excluded (listed by `coverage` with the reason): `docs/VIVA_NOTES.md`, `docs/DEVLOG.md` (personal notes), `docs/scripts/build_deliverables.md`, `docs/tests/test_build_deliverables.md` and the README section "Deliverables" (they describe the deliverable tooling itself, not the project). A `(from <file>)` suffix on a heading is removed. Headings are numbered by the script (`1`, `1.1`, `Appendix A`, `A.1`); the source's own leading numbers are removed. Header: project title. Footer: page number field (not on the title page). Code blocks: Courier New on a light grey background. Tables: borders, 9 pt text (inline code 9.5 pt), shaded bold header row repeated on every page, fixed layout with column widths from the content (below), rows may break across pages so tall rows leave no white gaps.

`config.json` (created if missing, never overwritten) has `student_name` (default "Aridhya Jain"), `register_no`, `course`, `faculty_name`, `institute`, `team_number`, `project_title` (default "MiniBox: A Mini Container Runtime") and `date`. Empty values appear on the title page as highlighted placeholders such as `[REGISTER NUMBER]`, and the build prints a warning listing them (once). The exception is `date`: if it is missing or empty, today's date is used, written like `19 October 2026`, without a warning; if it is set, it is used as written.

**Table column widths.** `compute_col_widths` starts each column proportional to its longest cell text, clamps it between 8% and 70% of the text width (9298 twips = 16.4 cm), never makes it narrower than its longest unbreakable token (a file path) where that fits (the floor is capped at the maximum and scaled down if all floors together exceed the page), redistributes the difference over the other columns (water-filling), and rounds so the widths add up to exactly the text width. They are written as `w:tblLayout fixed`, `w:tblW` in twips, `w:gridCol` and `w:tcW`.

**Build checks.** Besides leftovers, doc presence, numbering and the TOC, the build fails (exit 1) listing the problems if: a table is not fixed-layout, its widths do not add up to the text width, its header is not repeated or a row cannot break; a heading still has a `(from ...)` suffix; an excluded README section appears; or the "Test suite overview" table lacks a row for any `tests/*.sh` or `tests/test_*.py` (whose doc is not excluded), the missing names being printed. The summary prints the data-row count of the five largest tables.

## The PDF report (`report`)
`report` builds `~/minibox-deliverables/MiniBox_Report.pdf`: the whole project report (title page "Project Report", abstract, contents, chapters 1 to 10, references, appendices A to C). The text is **not** in the repo: it is Markdown in `~/minibox-deliverables/report/`, one file per chapter (`00_abstract.md`, `01_introduction.md` ... `10_conclusion.md`, see `REPORT_CHAPTERS`), one per appendix (`appendix_a_cli.md`, `appendix_b_layout.md`, `appendix_c_reproduce.md`, see `REPORT_APPENDICES`) and `references.md`, so it can be edited freely; the figures are the SVGs written by `scripts/make_report_diagrams.py` into `report/figures/` (`make deliverables-report` runs both steps). The script reads these files, assembles HTML and renders it with WeasyPrint; only the venv python has WeasyPrint, so the script re-runs itself under it (before printing anything), like `docs`.

**Markdown conventions** (the subset of `parse_markdown`, plus):
| Written as | Becomes |
|------------|---------|
| `# Title`, `## Title`, `### Title` | chapter `1`, section `1.1`, subsection `1.1.1` (numbers added by the script; deeper headings are an error; `00_abstract.md` is not numbered) |
| `## Title {#sec:name}` | the same heading, which can be referred to as `{sec:name}` -> "Section 4.3" |
| `![Caption](figures/x.svg){#fig:name}` on its own line | a figure with the caption "Figure 3.2: Caption" (numbered per chapter) |
| `Table: Caption  {#tab:name}` directly above a pipe table | the caption "Table 3.1: Caption" above the table (an error if no table follows) |
| `{fig:name}`, `{tab:name}`, `{sec:name}` | "Figure 3.2" etc., as a link to the target; an unknown name fails the build |
| `[ref:key]` | "[3]" as a link into the reference list; numbered by first citation; a key without a line in `references.md` fails the build |
| a line `key \| text` in `references.md` | one reference entry (`#` lines and blank lines ignored) |
| a heading `# Title` in an appendix file | `Appendix A`, `Appendix B`, ... (sections `A.1`, captions `Table A.1`, `Figure A.1`); the appendices come after the References in the document and in the contents |
| a line `{{gen:NAME}}` on its own (not inside a code fence) | the generated block `NAME`, expanded before parsing, see below |

**Generated blocks (`{{gen:NAME}}`).** No result table or file list of the report is typed by hand. `expand_generated` replaces such a line by Markdown made from the repo: `startup`, `memory`, `incremental`, `limits`, `limits-before` and `compare` call `startup_table`, `memory_table`, `incremental_table`, `limits_table` and `compare_table` of `bench/make_report_tables.py` on `bench/results/reference/` (`compare`: `reference-before-clone3` against `reference`); `machine` calls `machine_lines` on `system.json`; `probe` is `bench/results/analysis/attach_stall_probe.txt` as a code block; `tree:GROUP` (GROUP = `.`, `include`, `src`, `scripts`, `tests`, `ui`, `bench`, `docs`) is a table `File | Purpose` of the real file tree, where the purpose is the first sentence of the "Purpose" section of the matching `docs/<path>.md` (a file directly in `docs/` gives its title, a Markdown file without a doc its own first paragraph). Skipped: `venv`, `__pycache__`, `results`, `.cache`, `build`, `rootfs`, `.gitkeep`, `.gitignore`, the binary `minibox`, `.pyc/.o/.d`. A file without a purpose is left out and named by `cmd_report`. An unknown name or a missing file is a `BuildError`.

**Layout** (`REPORT_CSS`): A4, 2.5 cm margins, body Liberation Serif 11 pt (justified, hyphenated), code in DejaVu Sans Mono, headings and captions in Liberation Sans. Title page from `config.json` with the same placeholders (highlighted, with a warning) and date behaviour as the Word build; no header or footer on it (`@page :first`). Then the abstract, the contents (each entry ends with `leader('.')` and `target-counter(attr(href), page)`, so the page numbers are real), the chapters, the references, the appendices (each starts a new page). Running header: the project title; footer "Page X of Y" (`counter(page)`, `counter(pages)`). The first chapter starts a new page, the others follow on; headings and captions stay with what follows, tables and figures are not split and tables repeat their header row, code blocks wrap instead of overflowing (a block with a line over `WIDE_CODE_CHARS` = 85 characters gets `pre.wide`, a smaller font). A figure whose SVG is wider than `WIDE_FIGURE_UNITS` = 700 units (the benchmark charts) goes on its own landscape page (`@page wide`), because at the width of a portrait page its 12-pixel text would print at under 5 pt. In tables a file name in `code` keeps its width (`overflow-wrap: break-word`).

**Checks** (any failure prints all problems and exits 1; nothing is hidden):
- placeholder words `TODO`, `FIXME`, `TBD`, `XXX`, `???` in any source file (reported with file and line);
- the abstract has 150 to 250 words; every chapter file and every appendix file must exist;
- every `{fig:}`, `{tab:}`, `{sec:}` resolves and every `[ref:]` has an entry; a figure file must exist; a table caption must be followed by a table; names must be unique; no heading level skipped or deeper than 3;
- warnings only: a reference entry never cited, a figure or table never referred to;
- the fonts exist (`fc-list`);
- on the finished PDF (text read back with `pypdfium2`): page count between 30 and 60 (`REPORT_PAGES`); no title-page number, "Page 2 of N" on page 2; no leftover `{fig:`, `{tab:`, `[ref:`, `{{gen:` or placeholder word; every heading appears in the text; WeasyPrint reported no failed image load.
Finally every page is rendered to `~/minibox-deliverables/build/report_pages/page-NN.png` so the layout can be looked at (the build cannot judge overlapping text or bad page breaks by itself).

### Functions of the report build
| Signature | Inputs | Returns | Errors / side effects |
|-----------|--------|---------|-----------------------|
| `font_available(name)` | font family | True / False, None if `fc-list` is missing | runs `fc-list` |
| `count_words(text)` | text | number of words | none |
| `parse_references(text)` | contents of `references.md` | `{key: text}` in file order | `BuildError` for a line without `key \| text`, a bad key or a duplicate |
| `preprocess_report_md(text)` | Markdown | `(text with placeholder paragraphs, captions)` | none; ignores lines inside code fences |
| `parse_report_file(name, text, base_dir)` | one source file | blocks (`parse_markdown` blocks plus `figure`; table blocks carry `name` and `caption`) | `BuildError` for a table caption without a table |
| `number_report(chapters)` | list of `{file, kind, blocks}`, kind `abstract`, `chapter` or `appendix` | `{"fig:name": (label, anchor), ...}` (also `tab:` and `sec:`); numbers headings and captions in place | `BuildError` for a skipped or too deep heading, a duplicate name, a caption in the abstract |
| `Resolver(labels, refs)` with `.text()`, `.runs()`, `.cite_number()` | runs of inline text | HTML with links; `.cited` (keys in order of first use), `.used`, `.errors` | collects every problem instead of stopping at the first |
| `blocks_html(blocks, res, rdir)` | blocks | HTML (lists grouped, figures, captioned tables) | `BuildError` if a figure file is missing |
| `toc_entries(chapters, kind)` | chapters, `chapter` or `appendix` (or None) | `[(level, number, title, anchor)]` | none |
| `css_string(text)` | text | a CSS string literal | none |
| `title_page_html(shown)` | config values | the title page HTML; placeholders in a highlighted `span` | none |
| `load_bench_tables(repo)` | repo | the module `bench/make_report_tables.py` | none |
| `first_sentence(text)` | text | its first sentence | none |
| `purpose_of(rel, repo)` | repo-relative path | one-line purpose from the docs, `""` if none | none |
| `tree_files(group, repo)` / `tree_table(group, repo)` / `tree_omitted(repo)` | one of `TREE_GROUPS` | sorted paths / Markdown table / files with no purpose | `BuildError` for an unknown group or a group without a documented file |
| `generate_block(name, repo)` | directive name | Markdown (see Generated blocks) | `BuildError` for an unknown name or a missing result file |
| `expand_generated(text, repo)` | Markdown | the text with every `{{gen:NAME}}` line replaced (not inside code fences) | `BuildError` from `generate_block` |
| `svg_width(path)` | SVG file | width of its viewBox, 0 if none | none |
| `load_report_sources(rdir, repo)` | report folder, repo | `(chapters, references)`; directives expanded | `BuildError` if a source file is missing |
| `check_report_text(chapters)` | chapters | list of problems (placeholders, abstract length) | none |
| `check_page_count(pages, bounds)` | page count | list of problems (empty if within 30 to 60) | none |
| `build_report_html(rdir, shown, repo)` | report folder, config values, repo | dict: `html`, `problems`, `warnings`, `stats`, `chapters` | `BuildError` for a missing source or figure; other problems are returned |
| `pdf_pages_text(pdf)` / `render_pdf_pages(pdf, png_dir)` | the PDF | text per page / paths of the PNGs | `RepoWriteError` if `png_dir` is inside the repo (checked first) |
| `verify_report_pdf(pages_text, chapters)` | text of the pages | list of problems | none |
| `report_pdf_write(html, rdir, target)` | HTML, base folder for figures, PDF path | warnings logged by WeasyPrint | `RepoWriteError` if `target` is inside the repo (checked first) |
| `cmd_report(args)` | CLI args | 0 | `RepoWriteError` first; re-runs under the venv python; `BuildError` with the list of problems; writes `build/report.html`, the PDF and the page images; prints page count, figures, tables, cited references, words per file, files left out of Appendix B, warnings |

Pip pins for the venv are in `PIP_PDF` (`check-tools` prints the exact command if the report route is missing). `check-tools` now also prints the PDF report route (instead of the old "PDF (later task)" line), the LibreOffice render check of the Word file, and whether the three fonts are installed.

## Functions
| Signature | Inputs | Returns | Errors / side effects |
|-----------|--------|---------|-----------------------|
| `assert_outside_repo(path, repo=REPO)` | a path | the resolved `Path` | `RepoWriteError` if the path (after resolving symlinks) is the repo or inside it. Called before every write. |
| `ensure_config(out_dir, repo)` | output folder | path of `config.json` | creates the folder and the template only if missing; never overwrites |
| `load_config(out_dir, repo)` | output folder | dict of strings (defaults fill missing keys) | `BuildError` on invalid JSON |
| `config_values(cfg, today=None)` | config dict, optional date | `(values with placeholders, list of empty keys)` | none (pure). An empty/missing `date` becomes `today_text()` and is not listed as empty. |
| `today_text(today=None)` | optional `datetime.date` | `"19 October 2026"` | none (pure) |
| `probe_modules(out_dir, names)` | folder, module names | `{name: 'system' / 'venv' / None}` | runs the venv's python once to look |
| `choose_routes(tools, modules)` | found tools and modules | `{markdown_to_docx, svg_to_png, pdf, report_pdf}` | none (pure). SVG: `rsvg-convert`, else `cairosvg`. `pdf` (render check of the docx): LibreOffice, weasyprint, wkhtmltopdf, chromium, reportlab. `report_pdf`: weasyprint and pypdfium2 both present. |
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
| `main(argv)` | argv | exit code | 2 for a repo-path refusal, 1 for `BuildError`; subcommands `check-tools`, `coverage`, `docs`, `report` |

## System calls and library calls used and why
- `stat`/`open`/`read`/`write`/`mkdir` (through `pathlib`): reading the repo's Markdown, writing the docx, PNGs, `config.json`.
- `realpath` (`Path.resolve`): resolves `..` and symlinks so a path that points into the repo is recognised and refused.
- `fork`/`execve`/`wait` (through `subprocess`): `rsvg-convert`, `soffice`, `fc-list`, and the venv's python for module probing.
- WeasyPrint and pypdfium2 (libraries) read the font files and the SVGs and write the PDF; the page images are written with Pillow.
- `execv`: replaces the process with the venv's python when `python-docx` (docs) or `weasyprint` and `pypdfium2` (report) are only installed there (done before any output, otherwise every message would be printed by both processes).
- `which` (`shutil.which`, a `PATH` search with `stat`): tool detection.

## OS concept
Files and paths (path resolution, symlinks, the repo boundary as a safety rule), processes (running external tools, `exec`), isolated Python environments (venv) instead of system-wide installs, and exit codes as an interface.

## How to test
```sh
python3 -m unittest tests/test_build_deliverables.py -v     # no root, temp dirs only
python3 scripts/build_deliverables.py check-tools
python3 scripts/build_deliverables.py coverage              # must show missing: 0
python3 scripts/build_deliverables.py docs                  # prints "verification: OK"
python3 scripts/make_report_diagrams.py ; python3 scripts/build_deliverables.py report   # PDF, then look at build/report_pages/page-NN.png
python3 scripts/build_deliverables.py --out . docs ; echo $?   # run in the repo: refused, exit 2
python3 scripts/build_deliverables.py --out . report ; echo $? # refused, exit 2
```
Open `~/minibox-deliverables/MiniBox_Documentation.docx` in Word or LibreOffice; update the table of contents (right-click, Update field) to fill in page numbers.
