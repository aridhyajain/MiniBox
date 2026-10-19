# tests/test_build_deliverables.py

## Purpose
Unit tests (standard `unittest`, no root, temp directories only) for `scripts/build_deliverables.py` and `scripts/make_report_diagrams.py`. WeasyPrint is not needed: the report logic is tested as pure functions and the PDF itself is checked by running `make deliverables-report`.

## Tests
| Class | Checks |
|-------|--------|
| `ExclusionTests` | the exclusion list is exactly the two personal notes and the three deliverable-tooling docs (`build_deliverables`, its test doc, `make_report_diagrams`), each with a reason; they and the README section "Deliverables" are classified `excluded` (also in the real repo); the section is dropped from the README sections; an unmapped doc is still `included` (Appendix C) |
| `RepoGuardTests` | `assert_outside_repo` refuses the repo, a path inside it and a symlink that points into it, and allows other places; `main --out <repo>/x` exits 2 and creates nothing; `ensure_config` refuses the repo |
| `ConfigTests` | the template has all keys and the defaults (name, project title); an existing `config.json` is never overwritten and defaults fill missing keys; invalid JSON is an error; empty or blank values become `[REGISTER NUMBER]`-style placeholders and are listed for the warning; a full config gives no warning |
| `DateTests` | `today_text` gives `19 October 2026` (no leading zero); empty, blank or missing `date` becomes today and is not warned about; a set date is used as written |
| `TableWidthTests` | `compute_col_widths`: exact sum for many shapes, proportional when unclamped, clamped to min/max, never narrower than the longest token, short File column vs long Purpose column, infeasible floors, empty table |
| `HeadingProvenanceTests` | the `(from <file>)` suffix is removed and the rest kept, in parsed and added headings; the real model has no such suffix and no excluded section or doc |
| `TestOverviewTests` | expected rows are `tests/*.sh` and `tests/test_*.py` minus excluded docs; missing names are reported; the header is not a row; the real overview model has every row; the five largest tables are ranked |
| `CoverageTests` | in a fake repo: documented source is `included`, a source file without a doc and a missing fixed doc are `missing`, excluded docs are `excluded`, unknown docs go to Appendix C; group ordering; the real repo never includes an excluded doc |
| `LeftoverDetectorTests` | `**`, `#`, `\|---\|`, code fences, backticks, raw HTML tags, link and image syntax, list markers and `*italic*` are found; ordinary text with `<id>`, `*`, `<->` is not |
| `HeadingNumberTests` | consistent numbering (including appendices) passes; unnumbered, skipped, wrong-child, level-jump and duplicated numbers are reported; `number_headings` output passes the checker |
| `MarkdownTests` | inline markers disappear; table cells keep pipes in code spans; code blocks keep `#` and `**`; lists, nesting and continuation lines; heading shift and removal of the source's own numbers; `##` inside a code fence does not split sections |
| `RouteAndSvgTests` | route selection (SVG, PDF, none available); the report route needs both weasyprint and pypdfium2; CSS variables are resolved and the dark block removed |
| `ReportSyntaxTests` | figure and table-caption lines become blocks, a caption without a table is an error, the syntax inside a code block is left alone, `{#sec:name}` is split off a heading, `references.md` parsing (order, duplicates, malformed lines) |
| `CaptionNumberingTests` | "Figure 1.1", "Table 1.1" numbering per chapter and its restart in the next chapter; heading numbers `1`, `1.1`, `1.1.1`; the abstract is not numbered and may not hold captions; skipped level, duplicate name and a fourth level are errors; named sections |
| `ResolverTests` | `{fig:}`, `{tab:}`, `{sec:}` become numbered links; unresolved ones are collected (not hidden); citations are numbered by first use and repeated ones keep their number; a citation without an entry is an error; code spans are not resolved and HTML is escaped |
| `ReportChecksTests` | placeholder words (`TODO`, `FIXME`, `TBD`, `XXX`, `???`) are found with file and line; abstract length 150 to 250 words (edges); page count 30 to 60 (edges); checks on the PDF text (unresolved markers, footer, title page without number, missing heading) |
| `ReportHtmlTests` | a complete fake report folder gives HTML with the contents (`target-counter`), "Page X of Y", `@page :first`, the fonts, 2.5 cm margins, numbered captions and a reference list; several problems are reported together; unused reference or figure only warns; a missing figure file or source file fails; title-page placeholders are marked; CSS string escaping |
| `ReportRepoGuardTests` | `report --out <repo>/x` exits 2 and creates nothing; a symlink into the repo is refused; the PDF writer and the page renderer refuse the repo before importing anything |
| `GeneratedBlockTests` | in a fake repo with small result files: `startup`, `limits`, `compare`, `machine` and `probe` blocks are made from the files; `{{gen:...}}` is expanded outside code fences only; an unknown name, an unknown tree group or a missing result file is a `BuildError`; a report with a directive builds; a leftover `{{gen:` is caught by `RE_UNRESOLVED` |
| `TreeTests` | purpose from the doc (first sentence), from the title of a file in `docs/`, from a README's own first paragraph, empty without a doc; caches, `.gitkeep` and `results/` are skipped; files without a purpose are left out and listed by `tree_omitted`; every group of the real repo gives a table and only `ui/requirements.txt` has no purpose |
| `AppendixTests` | appendices are numbered `Appendix A`, `A.1`, `A.1.1`, captions `Table A.1`, sections `Section A.1`; the contents list chapters, then References, then appendices; the ten chapters and three appendices are counted, the subtitle is "Project Report"; a missing appendix file is an error; an unreferenced appendix table only warns |
| `DiagramTests` | the five SVGs are well formed and registered (including `ui_request_path`: `Host header check`, `hmac.compare_digest`, `401 unauthorized` ...); every expected label is present (for example `clone3(CLONE_NEWPID`, `pivot_root`, `memory.max`, `send SIGKILL`); only grays; at least three arrowheads each and no text under 12 units; `<id>` is escaped; the text-fit guard raises; `write_all` writes the five diagrams and the three charts (fixture files, copied) and refuses the repo (also via `main`, exit 2); a missing chart is an error |

## System calls used and why
Only file I/O in temp directories (`mkdir`, `write`, `symlink`).

## OS concept
Safety rules as tests: a path-resolution guard that keeps generated output out of the repository.

## How to test
`python3 -m unittest tests/test_build_deliverables.py -v` from the repository root.
