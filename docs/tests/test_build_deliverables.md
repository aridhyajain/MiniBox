# tests/test_build_deliverables.py

## Purpose
Unit tests (standard `unittest`, no root, temp directories only) for `scripts/build_deliverables.py`.

## Tests
| Class | Checks |
|-------|--------|
| `ExclusionTests` | the exclusion list is exactly the two personal notes and the two deliverable-tooling docs, each with a reason; they and the README section "Deliverables" are classified `excluded` (also in the real repo); the section is dropped from the README sections; an unmapped doc is still `included` (Appendix C) |
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
| `RouteAndSvgTests` | route selection (SVG, PDF, none available); CSS variables are resolved and the dark block removed |

## System calls used and why
Only file I/O in temp directories (`mkdir`, `write`, `symlink`).

## OS concept
Safety rules as tests: a path-resolution guard that keeps generated output out of the repository.

## How to test
`python3 -m unittest tests/test_build_deliverables.py -v` from the repository root.
