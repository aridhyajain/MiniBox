"""Unit tests for scripts/build_deliverables.py (standard library only, no root, temp dirs only).

Run:  python3 -m unittest tests/test_build_deliverables.py -v
"""
import contextlib
import datetime
import io
import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_deliverables as bd  # noqa: E402
import make_report_diagrams as mrd  # noqa: E402


def make_repo(root, files):
    """Create a fake repo: {relative path: text}."""
    for rel, text in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class ExclusionTests(unittest.TestCase):
    def test_exclusion_list(self):
        self.assertEqual(set(bd.EXCLUDED), {"docs/VIVA_NOTES.md", "docs/DEVLOG.md",
                                            "docs/scripts/build_deliverables.md",
                                            "docs/tests/test_build_deliverables.md",
                                            "docs/scripts/make_report_diagrams.md"})
        self.assertTrue(all(bd.EXCLUDED.values()))           # every exclusion has a reason
        self.assertEqual(set(bd.EXCLUDED_README_SECTIONS), {"Deliverables"})

    def test_tooling_docs_are_excluded_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            make_repo(d, {"README.md": "# x", "docs/scripts/build_deliverables.md": "# t",
                          "docs/tests/test_build_deliverables.md": "# t", "docs/tests/other.md": "# o"})
            res = bd.classify(d)
            for rel in ("docs/scripts/build_deliverables.md", "docs/tests/test_build_deliverables.md"):
                self.assertEqual(res[rel][0], "excluded")
                self.assertIn("tooling", res[rel][1])
            self.assertEqual(res["docs/tests/other.md"][0], "included")
            self.assertEqual(res[bd.README_SECTION_LABEL % "Deliverables"][0], "excluded")

    def test_readme_deliverables_section_is_dropped(self):
        _, secs = bd.md_sections("# t\n## Build\nb\n## Deliverables\nd\n## Documentation\nx\n")
        self.assertEqual(list(bd.drop_excluded_sections(secs)), ["Build", "Documentation"])

    def test_real_repo_lists_the_exclusions(self):
        res = bd.classify(ROOT)
        self.assertEqual(res["docs/scripts/build_deliverables.md"][0], "excluded")
        self.assertEqual(res["docs/tests/test_build_deliverables.md"][0], "excluded")
        self.assertEqual(res[bd.README_SECTION_LABEL % "Deliverables"][0], "excluded")

    def test_excluded_docs_are_classified_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            make_repo(d, {"README.md": "# x", "docs/VIVA_NOTES.md": "# v", "docs/DEVLOG.md": "# d",
                          "docs/notes.md": "# n"})
            res = bd.classify(d)
            self.assertEqual(res["docs/VIVA_NOTES.md"][0], "excluded")
            self.assertEqual(res["docs/DEVLOG.md"][0], "excluded")
            self.assertEqual(res["docs/notes.md"][0], "included")   # unmapped docs land in an appendix


class RepoGuardTests(unittest.TestCase):
    def test_refuses_inside_repo(self):
        with tempfile.TemporaryDirectory() as repo:
            with self.assertRaises(bd.RepoWriteError):
                bd.assert_outside_repo(Path(repo) / "out", repo)
            with self.assertRaises(bd.RepoWriteError):
                bd.assert_outside_repo(repo, repo)

    def test_refuses_symlink_into_repo(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as other:
            link = Path(other) / "link"
            link.symlink_to(repo)
            with self.assertRaises(bd.RepoWriteError):
                bd.assert_outside_repo(link / "x", repo)

    def test_allows_outside(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as other:
            self.assertEqual(bd.assert_outside_repo(other, repo), Path(other).resolve())

    def test_main_exits_2_and_writes_nothing_in_the_real_repo(self):
        target = ROOT / "should_not_exist_deliverables"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = bd.main(["--out", str(target), "check-tools"])
        self.assertEqual(code, 2)
        self.assertFalse(target.exists())
        self.assertIn("refusing", err.getvalue())

    def test_ensure_config_refuses_repo(self):
        with tempfile.TemporaryDirectory() as repo:
            with self.assertRaises(bd.RepoWriteError):
                bd.ensure_config(Path(repo) / "x", repo)
            self.assertFalse((Path(repo) / "x").exists())


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.TemporaryDirectory()
        self.out = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.repo.cleanup()
        self.out.cleanup()

    def test_creates_template_with_defaults(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cfg = bd.ensure_config(self.out.name, self.repo.name)
        data = json.loads(cfg.read_text())
        self.assertEqual(set(data), set(bd.CONFIG_DEFAULTS))
        self.assertEqual(data["student_name"], "Aridhya Jain")
        self.assertEqual(data["project_title"], "MiniBox: A Mini Container Runtime")

    def test_never_overwrites(self):
        cfg = Path(self.out.name) / "config.json"
        cfg.write_text('{"student_name": "Someone Else"}', encoding="utf-8")
        bd.ensure_config(self.out.name, self.repo.name)
        self.assertEqual(json.loads(cfg.read_text()), {"student_name": "Someone Else"})
        loaded = bd.load_config(self.out.name, self.repo.name)
        self.assertEqual(loaded["student_name"], "Someone Else")
        self.assertEqual(loaded["project_title"], "MiniBox: A Mini Container Runtime")  # default fills the gap

    def test_invalid_json_is_an_error(self):
        (Path(self.out.name) / "config.json").write_text("{nope", encoding="utf-8")
        with self.assertRaises(bd.BuildError):
            bd.load_config(self.out.name, self.repo.name)

    def test_placeholders_for_empty_values(self):
        cfg = dict(bd.CONFIG_DEFAULTS, register_no="  ", course="OS Lab")
        shown, empty = bd.config_values(cfg)
        self.assertEqual(shown["register_no"], "[REGISTER NUMBER]")
        self.assertEqual(shown["course"], "OS Lab")
        self.assertEqual(shown["student_name"], "Aridhya Jain")
        self.assertIn("register_no", empty)
        self.assertNotIn("course", empty)
        self.assertEqual(set(empty), {"register_no", "faculty_name", "institute", "team_number"})

    def test_full_config_has_no_warning(self):
        cfg = {k: "x" for k in bd.CONFIG_DEFAULTS}
        self.assertEqual(bd.config_values(cfg)[1], [])


class DateTests(unittest.TestCase):
    def test_today_text_format(self):
        self.assertEqual(bd.today_text(datetime.date(2026, 10, 19)), "19 October 2026")
        self.assertEqual(bd.today_text(datetime.date(2026, 3, 5)), "5 March 2026")     # no leading zero

    def test_empty_or_missing_date_is_today_and_no_warning(self):
        today = datetime.date(2026, 10, 19)
        for cfg in (dict(bd.CONFIG_DEFAULTS, date=""), dict(bd.CONFIG_DEFAULTS, date="   "),
                    {k: v for k, v in bd.CONFIG_DEFAULTS.items() if k != "date"}):
            shown, empty = bd.config_values(cfg, today)
            self.assertEqual(shown["date"], "19 October 2026")
            self.assertNotIn("date", empty)

    def test_date_that_is_set_is_used_as_written(self):
        shown, empty = bd.config_values(dict(bd.CONFIG_DEFAULTS, date="October 2026"), datetime.date(2030, 1, 1))
        self.assertEqual(shown["date"], "October 2026")
        self.assertNotIn("date", empty)


class TableWidthTests(unittest.TestCase):
    def check_sum_and_bounds(self, widths, total):
        self.assertEqual(sum(widths), total)
        self.assertTrue(all(w > 0 for w in widths))

    def test_sum_is_exact_for_many_shapes(self):
        for total in (9298, 9000, 10001):
            for rows in ([["a", "b"]], [["File", "Purpose"], ["x.c", "y" * 400]],
                         [["a"] * 7, ["b" * 30] * 7], [["only"]], [["a", "b", "c"], ["", "", "z" * 9]]):
                self.check_sum_and_bounds(bd.compute_col_widths(rows, total), total)

    def test_short_file_column_is_narrow_and_long_text_is_wide(self):
        rows = [["File", "Purpose", "Section"],
                ["tests/lib", "Helpers shared by several scripts. " * 12, "4.4.3"],
                ["tests/init_test", "Checks the init process. " * 10, "4.4.2"]]
        file_w, purpose_w, section_w = bd.compute_col_widths(rows, 9298)
        self.assertGreater(purpose_w, 2.5 * file_w)
        self.assertLessEqual(purpose_w, 9298 * bd.MAX_COL_PCT / 100 + 3)       # clamped at the maximum
        self.assertGreaterEqual(section_w, 9298 * bd.MIN_COL_PCT / 100 - 3)    # clamped at the minimum

    def test_clamped_between_min_and_max(self):
        rows = [["a", "b", "c"], ["x", "y" * 1000, "z"]]
        for w in bd.compute_col_widths(rows, 10000):
            self.assertGreaterEqual(w, 10000 * bd.MIN_COL_PCT / 100 - 3)
            self.assertLessEqual(w, 10000 * bd.MAX_COL_PCT / 100 + 3)

    def test_never_narrower_than_the_longest_token(self):
        path = "docs/include/some_really_long_name.md"          # 37 unbreakable characters
        rows = [["File", "Purpose"], [path, "word " * 200]]
        need = len(path) * bd.CHAR_TWIPS + bd.CELL_PAD_TWIPS
        self.assertGreaterEqual(bd.compute_col_widths(rows, 9298)[0], min(need, 9298 * bd.MAX_COL_PCT / 100) - 3)

    def test_proportional_when_nothing_is_clamped(self):
        rows = [[" ".join(["ab"] * k) for k in (7, 13, 20)]]            # short tokens, lengths 20, 38, 59
        w = bd.compute_col_widths(rows, 9000)
        lengths = [len(c) for c in rows[0]]
        for i in (1, 2):
            self.assertAlmostEqual(w[i] / w[0], lengths[i] / lengths[0], delta=0.02 * lengths[i] / lengths[0])

    def test_infeasible_floors_are_scaled_down_but_sum_holds(self):
        rows = [["x" * 60] * 4]                                  # four columns each wanting 70%
        w = bd.compute_col_widths(rows, 9298)
        self.assertEqual(sum(w), 9298)
        self.assertEqual(max(w) - min(w), 0 if 9298 % 4 == 0 else 1)

    def test_empty_table(self):
        self.assertEqual(bd.compute_col_widths([]), [])


class HeadingProvenanceTests(unittest.TestCase):
    def test_suffix_is_removed_rest_is_kept(self):
        self.assertEqual(bd.strip_provenance("Command usage and behaviour (from docs/src/commands.md)"),
                         "Command usage and behaviour")
        self.assertEqual(bd.strip_provenance("Wait (2) and signals (from a.md)"), "Wait (2) and signals")
        self.assertEqual(bd.strip_provenance("Plain (not provenance)"), "Plain (not provenance)")

    def test_parsed_and_added_headings_are_clean(self):
        blocks = bd.parse_markdown("## 2. Usage (from docs/x.md)\n", ".")
        self.assertEqual(blocks[0]["text"], "Usage")
        m = bd.Model(ROOT)
        m.h(3, "Command usage and behaviour (from docs/src/commands.md)")
        self.assertEqual(m.blocks[0]["text"], "Command usage and behaviour")

    def test_real_model_has_no_provenance_or_excluded_heading(self):
        m = bd.build_model(ROOT)
        heads = [b["text"] for b in m.blocks if b["t"] == "h"]
        self.assertFalse([h for h in heads if bd.RE_PROVENANCE.search(h)])
        self.assertIn("Command usage and behaviour", [h.split("  ", 1)[-1] for h in heads])
        self.assertNotIn("Deliverables", [h.split("  ", 1)[-1] for h in heads])
        self.assertNotIn("docs/scripts/build_deliverables.md", m.docs)
        self.assertNotIn("docs/tests/test_build_deliverables.md", m.docs)


class TestOverviewTests(unittest.TestCase):
    def test_expected_rows_cover_sh_and_test_py_and_skip_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            make_repo(d, {"tests/a_test.sh": "", "tests/lib.sh": "", "tests/test_x.py": "",
                          "tests/test_build_deliverables.py": "", "tests/helper.py": "", "tests/notes.txt": ""})
            self.assertEqual(bd.expected_test_rows(d), ["tests/a_test", "tests/lib", "tests/test_x"])

    def test_missing_rows_are_named(self):
        table = [["File", "Purpose", "Section"], ["tests/a", "p", "1"], ["tests/c", "p", "2"]]
        self.assertEqual(bd.missing_test_rows(table, ["tests/a", "tests/b", "tests/c", "tests/d"]),
                         ["tests/b", "tests/d"])
        self.assertEqual(bd.missing_test_rows(table, ["tests/a"]), [])

    def test_header_is_not_a_row(self):
        self.assertEqual(bd.missing_test_rows([["tests/a", "", ""]], ["tests/a"]), ["tests/a"])

    def test_real_overview_model_has_a_row_per_test_script(self):
        m = bd.build_model(ROOT)
        table = next(b for b in m.blocks if b["t"] == "table" and bd.runs_text(b["rows"][0][0]) == "File")
        rows = [[bd.runs_text(c) for c in r] for r in table["rows"]]
        self.assertEqual(bd.missing_test_rows(rows, bd.expected_test_rows(ROOT)), [])

    def test_largest_tables(self):
        tabs = [{"heading": "h%d" % n, "context": "h%d" % n, "rows": [["x"]] * (n + 1)} for n in (3, 9, 1, 7, 5, 8, 2)]
        top = bd.largest_tables(tabs)
        self.assertEqual([r for _, r in top], [9, 8, 7, 5, 3])


class CoverageTests(unittest.TestCase):
    def test_included_excluded_missing(self):
        with tempfile.TemporaryDirectory() as d:
            make_repo(d, {
                "README.md": "# r", "Makefile": "all:",
                "src/a.c": "", "src/b.c": "", "include/a.h": "", "scripts/s.sh": "",
                "docs/src/a.md": "# a", "docs/include/a.h": "x", "docs/include/a.md": "# ah",
                "docs/scripts/s.md": "# s", "docs/DEVLOG.md": "# d", "docs/extra.md": "# e",
            })
            res = bd.classify(d)
            self.assertEqual(res["docs/src/a.md"][0], "included")
            self.assertEqual(res["docs/src/b.md"][0], "missing")       # src/b.c has no doc
            self.assertEqual(res["docs/Makefile.md"][0], "missing")
            self.assertEqual(res["docs/DEVLOG.md"][0], "excluded")
            self.assertEqual(res["docs/extra.md"], ("included", "Appendix C (other documents)"))
            self.assertEqual(res["docs/ARCHITECTURE.md"][0], "missing")

    def test_group_docs_in_path_order(self):
        docs = ["docs/ui/z.md", "docs/ui/static/a.md", "docs/src/a.md"]
        self.assertEqual(bd.group_docs(docs, "ui"), ["docs/ui/static/a.md", "docs/ui/z.md"])

    def test_coverage_of_the_real_repo_has_no_unmapped_or_excluded_leak(self):
        res = bd.classify(ROOT)
        for path in bd.EXCLUDED:
            self.assertEqual(res[path][0], "excluded")
            self.assertEqual(res[path][1], bd.EXCLUDED[path])
        others = [p for p, (s, _) in res.items() if s == "included" and p in bd.EXCLUDED]
        self.assertEqual(others, [])


class LeftoverDetectorTests(unittest.TestCase):
    def test_detects_leftovers(self):
        for line, why in (("this is **bold**", "bold"), ("## Heading", "heading"), ("|---|---|", "table"),
                          ("| a | b |", None), ("```sh", "fence"), ("see `x`", "backtick"),
                          ("<br>", "HTML"), ("<div class='x'>", "HTML"), ("![alt](a.png)", "image"),
                          ("[t](http://x)", "link"), ("- item", "list marker"), ("an *emphasis* here", "italic")):
            found = bd.find_leftovers(line)
            if why is None:
                continue          # a lone cell separator is not a leftover by itself
            self.assertTrue(found, "expected a leftover in %r" % line)

    def test_clean_text_passes(self):
        for line in ("Plain sentence.", "Uses <id> and <pid> placeholders", "a * b = c", "rootfs/* is ignored",
                     "10.0.n.2 <-> 10.0.n.1", "Result: 3 - 2 = 1", "X + X - the classic interface"):
            self.assertEqual(bd.find_leftovers(line), [], line)


class HeadingNumberTests(unittest.TestCase):
    def test_consistent(self):
        heads = [(1, "1  A"), (2, "1.1  B"), (3, "1.1.1  C"), (2, "1.2  D"), (1, "2  E"), (2, "2.1  F"),
                 (1, "Appendix A  G"), (2, "A.1  H"), (1, "Appendix B  I")]
        self.assertEqual(bd.check_heading_numbers(heads), [])

    def test_detects_problems(self):
        self.assertTrue(bd.check_heading_numbers([(1, "Title without number")]))
        self.assertTrue(bd.check_heading_numbers([(1, "1  A"), (1, "3  B")]))            # skipped 2
        self.assertTrue(bd.check_heading_numbers([(1, "1  A"), (2, "1.2  B")]))          # should be 1.1
        self.assertTrue(bd.check_heading_numbers([(1, "1  A"), (3, "1.1.1  B")]))        # level jump
        self.assertTrue(bd.check_heading_numbers([(1, "1  A"), (2, "1.1  1. Dup")]))     # duplicated number

    def test_number_headings_output_is_consistent(self):
        blocks = [{"t": "h", "level": l, "text": t, "appendix": a} for l, t, a in
                  ((1, "A", False), (2, "B", False), (3, "C", False), (3, "D", False), (2, "E", False),
                   (1, "F", False), (1, "G", True), (2, "H", False), (1, "I", True))]
        bd.normalize_levels(blocks)
        bd.number_headings(blocks)
        self.assertEqual(bd.check_heading_numbers([(b["level"], b["text"]) for b in blocks]), [])
        self.assertEqual(blocks[3]["text"], "1.1.2  D")
        self.assertEqual(blocks[6]["text"], "Appendix A  G")


class MarkdownTests(unittest.TestCase):
    def parse(self, text, **kw):
        return bd.parse_markdown(text, ".", **kw)

    def test_inline_markers_are_removed(self):
        runs = bd.parse_inline("a **b `c`** and *d* [e](f.md) `x|y`")
        self.assertEqual(bd.runs_text(runs), "a b c and d e x|y")
        self.assertTrue(any(f["code"] for _, f in runs))

    def test_table_and_pipes_in_code(self):
        blocks = self.parse("| a | b |\n|---|---|\n| `x|y` | 2 |\n")
        self.assertEqual(blocks[0]["t"], "table")
        self.assertEqual(len(blocks[0]["rows"]), 2)
        self.assertEqual(bd.runs_text(blocks[0]["rows"][1][0]), "x|y")

    def test_code_block_keeps_markers(self):
        blocks = self.parse("```sh\n# comment\n**not bold**\n```\n")
        self.assertEqual(blocks, [{"t": "code", "text": "# comment\n**not bold**"}])

    def test_lists_and_continuation(self):
        blocks = self.parse("- one\n  continued\n  - nested\n1. first\n")
        self.assertEqual([b["t"] for b in blocks], ["li", "li", "li"])
        self.assertEqual(bd.runs_text(blocks[0]["runs"]), "one continued")
        self.assertEqual(blocks[1]["level"], 1)
        self.assertTrue(blocks[2]["ordered"])

    def test_headings_shift_and_number_stripping(self):
        blocks = self.parse("# T\n## 3. Old number\n", shift=1, drop_title=True)
        self.assertEqual(blocks, [{"t": "h", "level": 3, "text": "Old number"}])

    def test_md_sections_ignores_headings_in_code(self):
        pre, secs = bd.md_sections("# t\nintro\n## A\n```\n## not\n```\n## B\nx\n")
        self.assertEqual(list(secs), ["A", "B"])


class RouteAndSvgTests(unittest.TestCase):
    def test_routes(self):
        r = bd.choose_routes({}, {})
        self.assertEqual(r, {"markdown_to_docx": None, "svg_to_png": None, "pdf": None, "report_pdf": None})
        r = bd.choose_routes({"rsvg-convert": "/x", "soffice": "/y"}, {"docx": "venv", "cairosvg": "venv"})
        self.assertEqual(r["svg_to_png"], "rsvg-convert")
        self.assertIn("LibreOffice", r["pdf"])
        self.assertEqual(bd.choose_routes({}, {"cairosvg": "venv"})["svg_to_png"], "cairosvg")

    def test_report_route_needs_weasyprint_and_pypdfium2(self):
        self.assertIsNone(bd.choose_routes({}, {"weasyprint": "venv"})["report_pdf"])
        self.assertIsNone(bd.choose_routes({}, {"pypdfium2": "venv"})["report_pdf"])
        self.assertIn("weasyprint", bd.choose_routes({}, {"weasyprint": "venv", "pypdfium2": "venv"})["report_pdf"])

    def test_flatten_svg_resolves_variables_and_drops_dark_block(self):
        svg = ("<svg><style>svg { --bg:#ffffff; --fg:#111111; }\n@media (prefers-color-scheme: dark) {\n"
               "  svg { --bg:#000000; --fg:#eeeeee; }\n}\n.t { fill:var(--fg); }</style>"
               '<rect fill="var(--bg)"/></svg>')
        out = bd.flatten_svg(svg)
        self.assertIn('fill="#ffffff"', out)
        self.assertIn("fill:#111111", out)
        self.assertNotIn("var(", out)
        self.assertNotIn("#000000", out)


# ---------------------------------------------------------------- the PDF report

def blocks_of(text, name="x.md"):
    return bd.parse_report_file(name, text, ".")


def chapter(name, text, kind="chapter"):
    return {"file": name, "kind": kind, "text": text, "blocks": blocks_of(text, name)}


FIG = "![The architecture.](figures/a.svg){#fig:arch}"
TAB = "Table: A table.  {#tab:t}\n\n| A | B |\n|---|---|\n| 1 | 2 |"


class ReportSyntaxTests(unittest.TestCase):
    def test_figure_and_table_caption_lines_become_blocks(self):
        blocks = blocks_of("# C\n\n%s\n\n%s\n" % (FIG, TAB))
        kinds = [b["t"] for b in blocks]
        self.assertEqual(kinds, ["h", "figure", "table"])
        self.assertEqual((blocks[1]["name"], blocks[1]["path"]), ("arch", "figures/a.svg"))
        self.assertEqual((blocks[2]["name"], blocks[2]["caption"]), ("t", "A table."))

    def test_caption_without_a_table_is_an_error(self):
        with self.assertRaises(bd.BuildError):
            blocks_of("# C\n\nTable: Lonely.  {#tab:x}\n\nJust text.\n")
        with self.assertRaises(bd.BuildError):
            blocks_of("# C\n\nTable: Last.  {#tab:x}\n")

    def test_caption_syntax_inside_a_code_block_is_left_alone(self):
        blocks = blocks_of("# C\n\n```\n%s\n```\n" % FIG)
        self.assertEqual([b["t"] for b in blocks], ["h", "code"])

    def test_heading_name_is_split_off(self):
        blocks = blocks_of("# C\n\n## Title {#sec:why}\n")
        self.assertEqual((blocks[1]["text"], blocks[1]["name"]), ("Title", "why"))

    def test_references_file(self):
        refs = bd.parse_references("# comment\n\na | First `x`.\nb-2 | Second.\n")
        self.assertEqual(list(refs), ["a", "b-2"])
        for bad in ("nokey", "a | x\na | y", "a |", "bad key | x"):
            with self.assertRaises(bd.BuildError):
                bd.parse_references(bad)


class CaptionNumberingTests(unittest.TestCase):
    def numbered(self):
        c1 = chapter("1.md", "# One\n\n## A\n\n%s\n\n%s\n\n### Deep\n\n![Two.](figures/b.svg){#fig:two}\n" % (FIG, TAB))
        c2 = chapter("2.md", "# Two\n\n## B\n\n![Again.](figures/c.svg){#fig:three}\n\nTable: More.  {#tab:u}\n\n| A |\n|---|\n| 1 |\n")
        labels = bd.number_report([chapter("0.md", "# Abstract\n\ntext\n", "abstract"), c1, c2])
        return labels, c1, c2

    def test_figures_and_tables_are_numbered_per_chapter(self):
        labels, _, _ = self.numbered()
        self.assertEqual(labels["fig:arch"][0], "Figure 1.1")
        self.assertEqual(labels["fig:two"][0], "Figure 1.2")
        self.assertEqual(labels["tab:t"][0], "Table 1.1")
        self.assertEqual(labels["fig:three"][0], "Figure 2.1")      # numbering restarts in a new chapter
        self.assertEqual(labels["tab:u"][0], "Table 2.1")

    def test_headings_are_numbered(self):
        _, c1, c2 = self.numbered()
        nums = [b["num"] for b in c1["blocks"] + c2["blocks"] if b["t"] == "h"]
        self.assertEqual(nums, ["1", "1.1", "1.1.1", "2", "2.1"])

    def test_abstract_heading_is_not_numbered_and_captions_there_are_refused(self):
        ab = chapter("0.md", "# Abstract\n", "abstract")
        bd.number_report([ab])
        self.assertEqual(ab["blocks"][0]["num"], "")
        with self.assertRaises(bd.BuildError):
            bd.number_report([chapter("0.md", "# Abstract\n\n%s\n" % FIG, "abstract")])

    def test_skipped_level_duplicate_name_and_deep_heading_are_errors(self):
        for text in ("# A\n\n### skipped\n", "# A\n\n%s\n\n%s\n" % (FIG, FIG),
                     "# A\n\n## B\n\n### C\n\n#### D\n"):
            with self.assertRaises(bd.BuildError):
                bd.number_report([chapter("1.md", text)])

    def test_sections_can_be_named(self):
        labels = bd.number_report([chapter("1.md", "# A\n\n## B\n\n### C {#sec:deep}\n")])
        self.assertEqual(labels["sec:deep"][0], "Section 1.1.1")


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.labels = {"fig:arch": ("Figure 3.2", "fig-arch"), "tab:t": ("Table 1.1", "tab-t"),
                       "sec:s": ("Section 4.3", "sec-4-3")}
        self.refs = {"a": "First.", "b": "Second."}

    def test_cross_references_resolve_to_numbered_links(self):
        r = bd.Resolver(self.labels, self.refs)
        html = r.text("See {fig:arch}, {tab:t} and {sec:s}.")
        self.assertIn('<a class="xref" href="#fig-arch">Figure 3.2</a>', html)
        self.assertIn("Table 1.1</a>", html)
        self.assertIn("Section 4.3</a>", html)
        self.assertEqual(r.errors, [])
        self.assertEqual(r.used, {"fig:arch", "tab:t", "sec:s"})

    def test_unresolved_references_are_collected_not_hidden(self):
        r = bd.Resolver(self.labels, self.refs)
        out = r.text("{fig:nope} and {tab:also}")
        self.assertEqual(len(r.errors), 2)
        self.assertIn("{fig:nope}", out)

    def test_citations_are_numbered_by_first_use(self):
        r = bd.Resolver(self.labels, self.refs)
        html = r.text("x [ref:b] y [ref:a] z [ref:b]")
        self.assertEqual(re.findall(r">\[(\d)\]<", html), ["1", "2", "1"])
        self.assertEqual(r.cited, ["b", "a"])

    def test_citation_without_an_entry_is_an_error(self):
        r = bd.Resolver(self.labels, self.refs)
        r.text("[ref:missing]")
        self.assertEqual(len(r.errors), 1)
        self.assertIn("missing", r.errors[0])

    def test_code_spans_are_not_resolved_and_html_is_escaped(self):
        r = bd.Resolver(self.labels, self.refs)
        html = r.runs(bd.parse_inline("`{fig:nope}` and <b> & [ref:a]"))
        self.assertEqual(r.errors, [])
        self.assertIn("<code>{fig:nope}</code>", html)
        self.assertIn("&lt;b&gt; &amp;", html)


class ReportChecksTests(unittest.TestCase):
    def abstract(self, words):
        return chapter("00_abstract.md", "# Abstract\n\n%s\n" % " ".join(["word"] * words), "abstract")

    def test_placeholder_words_are_found_with_file_and_line(self):
        chs = [self.abstract(200), chapter("1.md", "# A\n\nfine\nTODO write this\n")]
        problems = bd.check_report_text(chs)
        self.assertEqual(len(problems), 1)
        self.assertIn("1.md line 4", problems[0])
        for word in ("FIXME", "TBD", "XXX", "???"):
            self.assertTrue(bd.check_report_text([self.abstract(200), chapter("1.md", "# A\n\n%s\n" % word)]))
        self.assertEqual(bd.check_report_text([self.abstract(200), chapter("1.md", "# A\n\nTODOS are fine? maybe.\n")]), [])

    def test_abstract_length(self):
        self.assertEqual(bd.check_report_text([self.abstract(150)]), [])
        self.assertEqual(bd.check_report_text([self.abstract(250)]), [])
        for n in (149, 251):
            self.assertTrue(any("abstract" in p for p in bd.check_report_text([self.abstract(n)])))

    def test_page_count_bounds(self):
        self.assertEqual(bd.REPORT_PAGES, (30, 60))
        self.assertEqual(bd.check_page_count(30), [])
        self.assertEqual(bd.check_page_count(60), [])
        self.assertTrue(bd.check_page_count(29))
        self.assertTrue(bd.check_page_count(61))

    def test_pdf_text_checks(self):
        chs = [chapter("1.md", "# Intro\n")]
        self.assertTrue(any("page count" in p for p in bd.verify_report_pdf(["t", "Page 2 of 3 Intro", "x"], chs)))
        pages = ["title"] + ["Page 2 of 30 Intro"] + ["text"] * 28
        self.assertEqual(bd.verify_report_pdf(pages, chs), [])
        bad = list(pages)
        bad[3] = "see {fig:x} and TODO and [ref:y]"
        problems = bd.verify_report_pdf(bad, chs)
        self.assertEqual(len(problems), 3)
        bad = ["Page 1 of 30"] + pages[1:]
        self.assertIn("the title page has a page number", bd.verify_report_pdf(bad, chs))
        self.assertTrue(any("footer" in p for p in bd.verify_report_pdf(["t", "no footer"] + ["x"] * 28, chs)))
        self.assertTrue(any("heading missing" in p for p in bd.verify_report_pdf(["t", "Page 2 of 30"] + ["x"] * 28, chs)))


def make_report_dir(root, chapters=None, extra=""):
    """A minimal valid report source folder (all files, one figure) under root."""
    rdir = Path(root) / "report"
    (rdir / "figures").mkdir(parents=True)
    (rdir / "figures" / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    files = {
        "00_abstract.md": "# Abstract\n\n" + " ".join(["word"] * 160) + "\n",
        "01_introduction.md": "# Introduction\n\n## Aim {#sec:aim}\n\nSee {fig:arch}, {tab:t} and [ref:a]. %s\n\n%s\n\n%s\n" % (extra, FIG, TAB),
        "02_background.md": "# Background\n\nBack to {sec:aim}.\n",
        "03_design.md": "# Design\n\nText.\n",
        "04_implementation.md": "# Implementation\n\n```\ncode\n```\n",
        "references.md": "a | Reference A.\n",
    }
    for i, name in enumerate(bd.REPORT_CHAPTERS[4:], 5):
        files[name] = "# Chapter %d\n\nText.\n" % i
    for i, name in enumerate(bd.REPORT_APPENDICES):
        files[name] = "# Appendix text %d\n\nText.\n" % (i + 1)
    files.update(chapters or {})
    for name, text in files.items():
        (rdir / name).write_text(text, encoding="utf-8")
    return rdir


SHOWN = dict(bd.CONFIG_DEFAULTS, project_title="Test Title", student_name="S", date="1 Jan 2026")


class ReportHtmlTests(unittest.TestCase):
    def test_end_to_end_html(self):
        with tempfile.TemporaryDirectory() as d:
            built = bd.build_report_html(make_report_dir(d), SHOWN)
            self.assertEqual(built["problems"], [])
            html = built["html"]
            self.assertIn("target-counter(attr(href), page)", html)        # contents with real page numbers
            self.assertIn('"Page " counter(page) " of " counter(pages)', html)
            self.assertIn("@page :first", html)                              # no header/footer on the title page
            self.assertIn("Liberation Serif", html)
            self.assertIn("DejaVu Sans Mono", html)
            self.assertIn("2.5cm", html)
            self.assertIn("Figure 1.1", html)
            self.assertIn('<h1 class="chapter" id="sec-references">References</h1>', html)
            self.assertIn('<li id="ref-a"><span class="n">[1]</span>Reference A.</li>', html)
            self.assertIn("Test Title", html)
            self.assertEqual(built["warnings"], [])
            self.assertEqual(built["stats"]["cited"], ["a"])

    def test_problems_are_reported_together(self):
        with tempfile.TemporaryDirectory() as d:
            rdir = make_report_dir(d, extra="{fig:ghost} [ref:ghost] TODO")
            problems = bd.build_report_html(rdir, SHOWN)["problems"]
            joined = "\n".join(problems)
            self.assertIn("{fig:ghost}", joined)
            self.assertIn("ref:ghost", joined)
            self.assertIn("TODO", joined)

    def test_unused_reference_and_figure_only_warn(self):
        with tempfile.TemporaryDirectory() as d:
            rdir = make_report_dir(d, chapters={"references.md": "a | A.\nb | B.\n",
                                                "01_introduction.md": "# I\n\n%s\n\n%s\n\nSee [ref:a].\n" % (FIG, TAB),
                                                "02_background.md": "# Background\n\nText.\n"})
            built = bd.build_report_html(rdir, SHOWN)
            self.assertEqual(built["problems"], [])
            self.assertEqual(len(built["warnings"]), 2)

    def test_missing_figure_file_and_missing_source_fail(self):
        with tempfile.TemporaryDirectory() as d:
            rdir = make_report_dir(d)
            (rdir / "figures" / "a.svg").unlink()
            with self.assertRaises(bd.BuildError) as cm:
                bd.build_report_html(rdir, SHOWN)
            self.assertIn("figure file not found", str(cm.exception))
        with tempfile.TemporaryDirectory() as d:
            rdir = make_report_dir(d)
            (rdir / "03_design.md").unlink()
            with self.assertRaises(bd.BuildError):
                bd.build_report_html(rdir, SHOWN)

    def test_title_page_placeholders_are_marked(self):
        shown, empty = bd.config_values({})
        html = bd.title_page_html(shown)
        self.assertIn('class="ph"', html)
        self.assertIn("[REGISTER NUMBER]", html)
        self.assertIn("register_no", empty)

    def test_css_string_escapes(self):
        self.assertEqual(bd.css_string('A "B" \\'), '"A \\"B\\" \\\\"')


class ReportRepoGuardTests(unittest.TestCase):
    def test_report_refuses_to_write_inside_the_repo(self):
        inside = bd.REPO / "report_must_not_exist"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = bd.main(["--out", str(inside), "report"])
        self.assertEqual(code, 2)
        self.assertIn("refusing", err.getvalue())
        self.assertFalse(inside.exists())
        with self.assertRaises(bd.RepoWriteError):
            bd.cmd_report(type("A", (), {"out": str(inside)})())

    def test_report_refuses_a_symlink_into_the_repo(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / "link"
            link.symlink_to(bd.REPO)
            with self.assertRaises(bd.RepoWriteError):
                bd.cmd_report(type("A", (), {"out": str(link / "x")})())

    def test_render_helpers_refuse_the_repo(self):
        with self.assertRaises(bd.RepoWriteError):
            bd.report_pdf_write("<html></html>", ".", bd.REPO / "x.pdf")
        with self.assertRaises(bd.RepoWriteError):
            bd.render_pdf_pages(bd.REPO / "x.pdf", bd.REPO / "pages")


def make_gen_repo(root):
    """A fake repo with result folders, a probe file and a few documented files, for {{gen:...}} tests."""
    head = "config,status,n,failed,min_ms,median_ms,mean_ms,p95_ms,max_ms,stdev_ms,note\n"
    row = "%s,measured,30,0,1.000,2.000,3.000,4.000,5.000,0.500,x\n"
    for folder, name in (("reference", "minibox_limits"), ("reference-before-clone3", "minibox_limits")):
        make_repo(root, {"bench/results/%s/startup_summary.csv" % folder: head + row % name,
                         "bench/results/%s/limits.csv" % folder: "test,expected,measured,tolerance,status,detail\n"
                                                              "memory/exit_code,137,137,exact,OK,\n"})
    make_repo(root, {
        "bench/results/reference/system.json": json.dumps({"date": "D", "kernel": "K", "cpu_model": "C",
                                                           "cpu_cores": 2, "ram_kb": 1048576, "wsl": True}),
        "bench/results/analysis/attach_stall_probe.txt": "ms per run\nrow clone3 9 10\n",
        "bench/make_report_tables.py": (bd.REPO / "bench" / "make_report_tables.py").read_text(),
        "bench/make_charts.py": (bd.REPO / "bench" / "make_charts.py").read_text(),
        "src/a.c": "int main(void);\n", "docs/src/a.md": "# src/a.c\n\n## Purpose\nDoes A. More text.\n",
        "src/b.c": "int b;\n",                                  # no document: left out of the table
        "README.md": "# Title\n\nThe readme says this. And more.\n",
        "docs/NOTES.md": "# Some notes\n\nBody.\n",
        "src/.gitkeep": "", "src/__pycache__/x.pyc": "", "bench/results/reference/startup.svg": "<svg/>"})


class GeneratedBlockTests(unittest.TestCase):
    def test_tables_come_from_the_result_files(self):
        with tempfile.TemporaryDirectory() as d:
            make_gen_repo(d)
            startup = bd.generate_block("startup", d)
            self.assertIn("| minibox_limits | 30 | 1.000 | 2.000 | 3.000 | 4.000 | 0.500 |", startup)
            self.assertIn("| memory/exit_code | 137 | 137 | OK |", bd.generate_block("limits", d))
            self.assertIn("| minibox_limits | before | 30 | 2.000 |", bd.generate_block("compare", d))
            self.assertIn("- Kernel: K", bd.generate_block("machine", d))
            self.assertEqual(bd.generate_block("probe", d), "```\nms per run\nrow clone3 9 10\n```")

    def test_expand_replaces_directive_lines_only_outside_code(self):
        with tempfile.TemporaryDirectory() as d:
            make_gen_repo(d)
            text = "Before\n{{gen:limits}}\nAfter\n```\n{{gen:limits}}\n```\n"
            out = bd.expand_generated(text, d)
            self.assertEqual(out.count("| Check | Expected | Measured | Status |"), 1)
            self.assertIn("```\n{{gen:limits}}\n```", out)

    def test_unknown_directive_and_missing_file_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            make_gen_repo(d)
            for bad in ("nonsense", "tree:nowhere", "memory"):         # memory.csv does not exist in the fake repo
                with self.assertRaises(bd.BuildError):
                    bd.generate_block(bad, d)
            with self.assertRaises(bd.BuildError):
                bd.expand_generated("{{gen:nonsense}}", d)

    def test_report_with_a_directive_builds_and_leftover_marker_is_detected(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as r:
            make_gen_repo(r)
            rdir = make_report_dir(d, chapters={"03_design.md": "# Design\n\nTable: Limits.  {#tab:lim}\n\n{{gen:limits}}\n\nSee {tab:lim}.\n"})
            built = bd.build_report_html(rdir, SHOWN, repo=r)
            self.assertEqual(built["problems"], [])
            self.assertIn("memory/exit_code", built["html"])
        self.assertTrue(bd.RE_UNRESOLVED.search("text {{gen:limits}}"))


class TreeTests(unittest.TestCase):
    def test_purpose_comes_from_the_doc_a_title_or_the_file_itself(self):
        with tempfile.TemporaryDirectory() as d:
            make_gen_repo(d)
            self.assertEqual(bd.purpose_of("src/a.c", d), "Does A.")           # first sentence of Purpose
            self.assertEqual(bd.purpose_of("docs/NOTES.md", d), "Some notes")   # a top-level document: its title
            self.assertEqual(bd.purpose_of("README.md", d), "The readme says this.")
            self.assertEqual(bd.purpose_of("src/b.c", d), "")                   # no document

    def test_tree_skips_caches_markers_and_undocumented_files(self):
        with tempfile.TemporaryDirectory() as d:
            make_gen_repo(d)
            self.assertEqual(bd.tree_files("src", d), ["src/a.c", "src/b.c"])
            table = bd.tree_table("src", d)
            self.assertIn("| `src/a.c` | Does A. |", table)
            self.assertNotIn("src/b.c", table)
            self.assertEqual(bd.tree_omitted(d), ["src/b.c", "bench/make_charts.py", "bench/make_report_tables.py"])
            self.assertEqual(bd.tree_files("bench", d), ["bench/make_charts.py", "bench/make_report_tables.py"])  # no results/
            with self.assertRaises(bd.BuildError):
                bd.tree_files("nowhere", d)

    def test_real_repository_tables_are_complete(self):
        for group in bd.TREE_GROUPS:
            table = bd.tree_table(group)
            self.assertTrue(table.startswith("| File | Purpose |"))
        self.assertEqual(bd.tree_omitted(), [])


class AppendixTests(unittest.TestCase):
    def numbered(self):
        chs = [chapter("0.md", "# Abstract\n\nt\n", "abstract"),
               chapter("1.md", "# One\n\n## A\n\n%s\n" % FIG),
               chapter("a.md", "# Commands\n\n## Synopsis {#sec:syn}\n\n%s\n\n### Deep\n" % TAB.replace("tab:t", "tab:at"), "appendix"),
               chapter("b.md", "# Layout\n\n## Files\n\nText\n", "appendix")]
        return bd.number_report(chs), chs

    def test_appendices_are_lettered_after_the_chapters(self):
        labels, chs = self.numbered()
        nums = [b["num"] for c in chs for b in c["blocks"] if b["t"] == "h"]
        self.assertEqual(nums, ["", "1", "1.1", "Appendix A", "A.1", "A.1.1", "Appendix B", "B.1"])
        self.assertEqual(labels["tab:at"][0], "Table A.1")
        self.assertEqual(labels["fig:arch"][0], "Figure 1.1")
        self.assertEqual(labels["sec:syn"][0], "Section A.1")

    def test_toc_lists_chapters_then_references_then_appendices(self):
        with tempfile.TemporaryDirectory() as d:
            built = bd.build_report_html(make_report_dir(d), SHOWN)
            toc = built["stats"]["toc"]
            titles = [t for _, t in toc]
            self.assertEqual(built["stats"]["chapters"], 10)
            self.assertEqual(built["stats"]["appendices"], 3)
            self.assertEqual(titles.index("References"), titles.index("Chapter 10") + 1)
            self.assertEqual([n for n, _ in toc if n.startswith("Appendix")], ["Appendix A", "Appendix B", "Appendix C"])
            self.assertEqual(titles[-1], "Appendix text 3")
            self.assertIn('<section class="appendix">', built["html"])
            self.assertIn("Project Report</div>", built["html"])
            self.assertNotIn("Part 1", built["html"])

    def test_missing_appendix_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            rdir = make_report_dir(d)
            (rdir / bd.REPORT_APPENDICES[2]).unlink()
            with self.assertRaises(bd.BuildError):
                bd.build_report_html(rdir, SHOWN)

    def test_unreferenced_appendix_table_only_warns(self):
        with tempfile.TemporaryDirectory() as d:
            rdir = make_report_dir(d, chapters={bd.REPORT_APPENDICES[0]: "# Commands\n\n%s\n" % TAB.replace("tab:t", "tab:at")})
            built = bd.build_report_html(rdir, SHOWN)
            self.assertEqual(built["problems"], [])
            self.assertTrue(any("tab:at" in w for w in built["warnings"]))


class DiagramTests(unittest.TestCase):
    NAMED = {
        "architecture": ["Web UI", "minibox CLI", "Monitor process", "Container init", "State directory", "cgroup",
                         "veth pair", "clone3"],
        "startup_sequence": ["clone3(CLONE_NEWPID", "CLONE_INTO_CGROUP", "pivot_root", "'R' on the ready pipe",
                             "'G' on the go pipe", "execvp(command)", "minit_run", "waitpid(container)",
                             "mkdir minibox/box-<id>"],
        "cgroup_hierarchy": ["/sys/fs/cgroup", "minibox/", "box-<container id>/", "memory.max", "cpu.max",
                             "pids.max", "cgroup.procs"],
        "lifecycle": ["registered", "running", "exited", "dead", "removed", "send SIGTERM", "send SIGKILL"],
        "ui_request_path": ["Host header check", "hmac.compare_digest", "16 KB cap", "Input validation",
                            "At most 10 running containers", "argument list, no shell", "400 bad_host",
                            "401 unauthorized", "409 limit reached"],
    }

    def parse(self, name):
        return ET.fromstring(mrd.BUILDERS[name]())

    def test_all_diagrams_are_well_formed_and_named(self):
        self.assertEqual(set(mrd.BUILDERS), set(mrd.FIGURES))
        for name in mrd.FIGURES:
            root = self.parse(name)
            self.assertTrue(root.tag.endswith("svg"))
            self.assertTrue(root.get("viewBox"))

    def test_expected_labels_are_present(self):
        for name, labels in self.NAMED.items():
            text = " ".join("".join(t.itertext()) for t in self.parse(name).iter("{http://www.w3.org/2000/svg}text"))
            for label in labels:
                self.assertIn(label, text, "%s: %r missing" % (name, label))

    def test_grayscale_only(self):
        for name in mrd.FIGURES:
            svg = mrd.BUILDERS[name]()
            for colour in set(re.findall(r'(?:fill|stroke)="(#[0-9a-fA-F]{6})"', svg)):
                r, g, b = colour[1:3], colour[3:5], colour[5:7]
                self.assertEqual((r, g), (g, b), "%s uses the colour %s" % (name, colour))

    def test_every_figure_has_arrowheads_and_readable_text(self):
        for name in mrd.FIGURES:
            root = self.parse(name)
            heads = [e for e in root.iter("{http://www.w3.org/2000/svg}polygon") if e.get("class") == "arrowhead"]
            self.assertGreaterEqual(len(heads), 3, name)
            for t in root.iter("{http://www.w3.org/2000/svg}text"):
                self.assertGreaterEqual(float(t.get("font-size")), 12, name)

    def test_text_is_escaped(self):
        for name in mrd.FIGURES:
            self.assertNotIn("<id>", mrd.BUILDERS[name]())     # would be an element, must be &lt;id&gt;

    def test_text_fit_guard(self):
        s = mrd.Svg(50, "t", "d")
        with self.assertRaises(ValueError):
            s.text(10, 10, "x" * 200, fit=50)

    def test_write_all_and_repo_guard(self):
        with tempfile.TemporaryDirectory() as d:
            charts = Path(d) / "charts"
            charts.mkdir()
            for name in mrd.CHARTS:
                (charts / (name + ".svg")).write_text("<svg>" + name * 200 + "</svg>")
            paths = mrd.write_all(Path(d) / "figures", chart_source=charts)
            self.assertEqual(sorted(p.name for p in paths), sorted(n + ".svg" for n in mrd.FIGURES + mrd.CHARTS))
            self.assertTrue(all(p.stat().st_size > 500 for p in paths))
            self.assertEqual((Path(d) / "figures" / "memory.svg").read_text(), (charts / "memory.svg").read_text())
            with self.assertRaises(ValueError):          # a missing chart is an error, not a silent gap
                mrd.write_all(Path(d) / "other", chart_source=Path(d) / "nowhere")
        with self.assertRaises(bd.RepoWriteError):
            mrd.write_all(bd.REPO / "figs_must_not_exist", chart_source=charts)
        self.assertFalse((bd.REPO / "figs_must_not_exist").exists())
        self.assertEqual(mrd.main(["--out", str(bd.REPO / "figs_must_not_exist")]), 2)


if __name__ == "__main__":
    unittest.main()
