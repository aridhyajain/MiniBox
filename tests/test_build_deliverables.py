"""Unit tests for scripts/build_deliverables.py (standard library only, no root, temp dirs only).

Run:  python3 -m unittest tests/test_build_deliverables.py -v
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_deliverables as bd  # noqa: E402


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
                                            "docs/tests/test_build_deliverables.md"})
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
        self.assertEqual(r, {"markdown_to_docx": None, "svg_to_png": None, "pdf": None})
        r = bd.choose_routes({"rsvg-convert": "/x", "soffice": "/y"}, {"docx": "venv", "cairosvg": "venv"})
        self.assertEqual(r["svg_to_png"], "rsvg-convert")
        self.assertIn("LibreOffice", r["pdf"])
        self.assertEqual(bd.choose_routes({}, {"cairosvg": "venv"})["svg_to_png"], "cairosvg")

    def test_flatten_svg_resolves_variables_and_drops_dark_block(self):
        svg = ("<svg><style>svg { --bg:#ffffff; --fg:#111111; }\n@media (prefers-color-scheme: dark) {\n"
               "  svg { --bg:#000000; --fg:#eeeeee; }\n}\n.t { fill:var(--fg); }</style>"
               '<rect fill="var(--bg)"/></svg>')
        out = bd.flatten_svg(svg)
        self.assertIn('fill="#ffffff"', out)
        self.assertIn("fill:#111111", out)
        self.assertNotIn("var(", out)
        self.assertNotIn("#000000", out)


if __name__ == "__main__":
    unittest.main()
