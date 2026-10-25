#!/usr/bin/env python3
"""Build the final deliverables (Word documentation now, PDF later) OUTSIDE the repository.

Subcommands:
  check-tools   which converters exist and which route is chosen
  coverage      every docs/**/*.md file: included, excluded or missing
  docs          build ~/minibox-deliverables/MiniBox_Documentation.docx and verify it
  report        build ~/minibox-deliverables/MiniBox_Report.pdf (the whole project report) and verify it

Everything is read from the repo's existing files; nothing is written inside the repo.
Details: docs/scripts/build_deliverables.md
"""
import argparse
import datetime
import html as htmllib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path.home() / "minibox-deliverables"

# Never part of the Word file: path (relative to the repo root) -> reason shown by `coverage`.
EXCLUDED = {
    "docs/VIVA_NOTES.md": "personal notes",
    "docs/DEVLOG.md": "personal notes",
    "docs/scripts/build_deliverables.md": "documents the deliverable tooling itself, not the project",
    "docs/tests/test_build_deliverables.md": "documents the deliverable tooling itself, not the project",
    "docs/scripts/make_report_diagrams.md": "documents the deliverable tooling itself, not the project",
}
# README sections (level-2 heading prefix) left out for the same reason.
EXCLUDED_README_SECTIONS = {
    "Deliverables": "describes the deliverable tooling itself, not the project",
}
README_SECTION_LABEL = "README.md (section '%s')"

# Page geometry (A4, 2.3 cm margins) shared by the page setup and the table width calculation.
PAGE_WIDTH_CM = 21.0
MARGIN_CM = 2.3
TEXT_WIDTH_CM = PAGE_WIDTH_CM - 2 * MARGIN_CM
TEXT_WIDTH_TWIPS = int(round(TEXT_WIDTH_CM / 2.54 * 1440))   # 1 twip = 1/1440 inch

# Pinned pip packages (installed into the venv in the deliverables folder, never system-wide).
PIP_DOCX = ["python-docx==1.1.2", "lxml==6.1.3", "typing_extensions==4.16.0"]
PIP_SVG = ["cairosvg==2.8.2", "cairocffi==1.7.1", "cffi==2.1.1", "pycparser==3.11", "cssselect2==0.10.1",
           "tinycss2==1.5.1", "webencodings==0.6.1", "defusedxml==0.7.1", "pillow==13.0.0"]

PIP_PDF = ["weasyprint==70.0", "pypdfium2==5.14.0", "pillow==13.0.0", "pydyf==0.13.0", "pyphen==0.18.1",
           "tinycss2==1.5.1", "cssselect2==0.10.1", "fonttools==4.67.0", "brotli==1.2.0", "zopfli==0.4.3"]

CONFIG_DEFAULTS = {
    "student_name": "Aridhya Jain",
    "register_no": "",
    "course": "",
    "faculty_name": "",
    "institute": "",
    "team_number": "",
    "project_title": "MiniBox: A Mini Container Runtime",
    "date": "",
}
PLACEHOLDERS = {
    "student_name": "[STUDENT NAME]", "register_no": "[REGISTER NUMBER]", "course": "[COURSE]",
    "faculty_name": "[FACULTY NAME]", "institute": "[INSTITUTE]", "team_number": "[TEAM NUMBER]",
    "project_title": "[PROJECT TITLE]", "date": "[DATE]",
}

DOCX_NAME = "MiniBox_Documentation.docx"

# Where each environment variable is documented (the text is taken from those docs).
ENV_VARS = (
    ("MINIBOX_STATE_DIR", "Root of the container state directory. Default `/var/lib/minibox/containers`; "
     "the tests and the benchmarks point it at a temporary directory.", "docs/src/state.md"),
    ("MINIBOX_NO_CLONE3", "If set to `1`, `run` uses `clone()` and then writes the PID to `cgroup.procs` "
     "instead of `clone3(CLONE_INTO_CGROUP)`. A diagnostic switch for benchmarks and tests.", "docs/src/container.md"),
    ("MINIBOX_BIN", "Web UI only: path of the `minibox` binary the UI runs (default `<repo>/minibox`).",
     "docs/ui/minibox_client.md"),
    ("MINIBOX_UI_TOKEN", "Web UI only: sets a known access token (at least 16 characters; "
     "normally the token is random and only the test script sets this).", "docs/ui/app.md"),
)

PROGRAM_GROUPS = ("include", "src", "scripts", "tests", "ui", "bench")


class BuildError(Exception):
    """A problem that must stop the build (printed, exit 1)."""


class RepoWriteError(BuildError):
    """An output path inside the repository (printed, exit 2)."""


# ---------------------------------------------------------------- paths and config

def assert_outside_repo(path, repo=REPO):
    """Resolve `path` (following symlinks) and refuse it if it is the repo or inside it."""
    p = Path(path).expanduser().resolve()
    r = Path(repo).resolve()
    if p == r or r in p.parents:
        raise RepoWriteError("refusing to write inside the repository: %s (repo: %s)" % (p, r))
    return p


def ensure_config(out_dir, repo=REPO):
    """Create config.json from the defaults if it is missing; never overwrite. Returns its path."""
    out = assert_outside_repo(out_dir, repo)
    out.mkdir(parents=True, exist_ok=True)
    cfg = out / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps(CONFIG_DEFAULTS, indent=2) + "\n", encoding="utf-8")
        print("created %s (fill in the empty values)" % cfg)
    return cfg


def load_config(out_dir, repo=REPO):
    """Read config.json; missing keys fall back to the defaults. Returns a dict of strings."""
    cfg = ensure_config(out_dir, repo)
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BuildError("%s is not valid JSON: %s" % (cfg, e))
    if not isinstance(data, dict):
        raise BuildError("%s must contain a JSON object" % cfg)
    merged = dict(CONFIG_DEFAULTS)
    merged.update({k: str(v) for k, v in data.items() if k in CONFIG_DEFAULTS and v is not None})
    return merged


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def today_text(today=None):
    """Today's date as '19 October 2026' (day without a leading zero, English month name)."""
    d = today or datetime.date.today()
    return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)


def config_values(cfg, today=None):
    """Return (display values with placeholders for empty ones, list of keys that are empty).

    An empty or missing "date" is not a problem: it becomes today's date and is not listed as empty.
    A date that is set is used exactly as written.
    """
    shown, empty = {}, []
    for key in CONFIG_DEFAULTS:
        v = cfg.get(key, "").strip()
        if key == "date" and not v:
            v = today_text(today)
        if v:
            shown[key] = v
        else:
            shown[key] = PLACEHOLDERS[key]
            empty.append(key)
    return shown, empty


# ---------------------------------------------------------------- tools

TOOL_NAMES = ("pandoc", "soffice", "libreoffice", "rsvg-convert", "wkhtmltopdf", "chromium",
              "chromium-browser", "google-chrome")
MODULE_NAMES = ("docx", "cairosvg", "weasyprint", "pypdfium2", "reportlab")


def venv_python(out_dir):
    return Path(out_dir).expanduser() / "venv" / "bin" / "python"


def probe_modules(out_dir, names=MODULE_NAMES):
    """Map module name -> 'system' / 'venv' / None (the venv is probed in a subprocess)."""
    result = {n: ("system" if importlib.util.find_spec(n) else None) for n in names}
    vpy = venv_python(out_dir)
    if vpy.exists() and Path(sys.prefix).resolve() != vpy.parent.parent.resolve():
        code = ("import importlib.util,json,sys;"
                "print(json.dumps({n: bool(importlib.util.find_spec(n)) for n in sys.argv[1:]}))")
        try:
            r = subprocess.run([str(vpy), "-I", "-c", code, *names], capture_output=True, text=True, timeout=60)
            found = json.loads(r.stdout) if r.returncode == 0 else {}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            found = {}
        for n in names:
            if result[n] is None and found.get(n):
                result[n] = "venv"
    elif vpy.exists():
        pass  # already running inside the venv: find_spec above covered it
    return result


def choose_routes(tools, modules):
    """Pick the Markdown->docx, SVG->PNG and PDF routes from what exists (pure function)."""
    md = "python-docx + built-in Markdown parser" if modules.get("docx") else None
    if tools.get("rsvg-convert"):
        svg = "rsvg-convert"
    elif modules.get("cairosvg"):
        svg = "cairosvg"
    else:
        svg = None
    office = tools.get("soffice") or tools.get("libreoffice")
    if office:
        pdf = "LibreOffice (docx -> pdf)"
    elif modules.get("weasyprint"):
        pdf = "weasyprint"
    elif tools.get("wkhtmltopdf"):
        pdf = "wkhtmltopdf"
    elif tools.get("chromium") or tools.get("chromium-browser") or tools.get("google-chrome"):
        pdf = "chromium (headless print to pdf)"
    elif modules.get("reportlab"):
        pdf = "reportlab (drawn from the document model)"
    else:
        pdf = None
    report = "weasyprint + pypdfium2 (page check)" if modules.get("weasyprint") and modules.get("pypdfium2") else None
    return {"markdown_to_docx": md, "svg_to_png": svg, "pdf": pdf, "report_pdf": report}


def venv_commands(out_dir):
    base = str(Path(out_dir).expanduser())
    return ["python3 -m venv %s/venv" % base,
            "%s/venv/bin/pip install %s" % (base, " ".join(PIP_DOCX))]


def cmd_check_tools(args):
    out = Path(args.out).expanduser()
    tools = {t: shutil.which(t) for t in TOOL_NAMES}
    modules = probe_modules(out)
    print("External tools:")
    for t in TOOL_NAMES:
        print("  %-17s %s" % (t, tools[t] or "missing"))
    print("Python modules (system python or %s/venv):" % out)
    labels = {"docx": "python-docx", "cairosvg": "cairosvg", "weasyprint": "weasyprint", "pypdfium2": "pypdfium2",
              "reportlab": "reportlab"}
    for m in MODULE_NAMES:
        print("  %-17s %s" % (labels[m], modules[m] or "missing"))
    routes = choose_routes(tools, modules)
    print("Chosen routes:")
    print("  Markdown -> docx : %s" % (routes["markdown_to_docx"] or "NONE"))
    print("  SVG -> PNG       : %s" % (routes["svg_to_png"] or "NONE"))
    print("  PDF report       : %s" % (routes["report_pdf"] or "NONE (needs weasyprint and pypdfium2 in the venv)"))
    print("  Word render check: %s" % ((tools["soffice"] or tools["libreoffice"]) and "LibreOffice (docx -> pdf)" or "NONE (optional)"))
    print("Fonts of the PDF report (fc-list):")
    for f in REPORT_FONTS:
        have = font_available(f)
        print("  %-17s %s" % (f, "unknown (fc-list not found)" if have is None else ("found" if have else "MISSING")))
    print("  (pandoc is %s; the docx route does not use it)" % ("present" if tools["pandoc"] else "absent"))
    todo = []
    if not routes["markdown_to_docx"]:
        todo += venv_commands(out)
    if not routes["svg_to_png"]:
        todo.append("sudo apt install librsvg2-bin      # preferred, or without root:")
        todo.append("%s/venv/bin/pip install %s" % (out, " ".join(PIP_SVG)))
    if todo:
        print("Run these commands (this script never installs anything):")
        for c in todo:
            print("  " + c)
    if not routes["report_pdf"]:
        print("For the PDF report (no sudo): %s/venv/bin/pip install %s" % (out, " ".join(PIP_PDF)))
    if not (tools["soffice"] or tools["libreoffice"]):
        print("Optional (render check of the Word file): sudo apt install libreoffice-writer")
    return 0 if routes["markdown_to_docx"] and routes["svg_to_png"] else 1


# ---------------------------------------------------------------- document inventory and coverage

def all_docs(repo=REPO):
    """Every Markdown file under docs/, as sorted repo-relative posix paths."""
    return sorted(p.relative_to(repo).as_posix() for p in (Path(repo) / "docs").rglob("*.md"))


def expected_docs(repo=REPO):
    """Docs the project rules require: one per source file, mirroring its path."""
    repo = Path(repo)
    want = []
    patterns = (("src", "*.c"), ("include", "*.h"), ("scripts", "*.sh"), ("scripts", "*.py"),
                ("tests", "*.sh"), ("tests", "*.py"), ("ui", "*.py"), ("ui/static", "*"),
                ("ui/templates", "*"), ("bench", "*.py"), ("bench", "*.sh"))
    for d, pat in patterns:
        for f in sorted((repo / d).glob(pat)):
            if f.is_file():
                want.append("docs/%s/%s.md" % (d, f.stem))
    if (repo / "Makefile").exists():
        want.append("docs/Makefile.md")
    return sorted(set(want))


def fixed_docs():
    """Docs the deliverable always uses by name."""
    return ["README.md", "docs/ARCHITECTURE.md", "docs/BENCHMARKS.md", "docs/REVIEW1.md", "docs/REVIEW2.md",
            "docs/Makefile.md"]


def group_docs(docs, group):
    """Docs of one program group: docs/<group>/**/*.md in path order."""
    prefix = "docs/%s/" % group
    return sorted(d for d in docs if d.startswith(prefix))


def classify(repo=REPO):
    """Return {path: (status, where)} for docs/**/*.md and anything required but absent.

    status: included / excluded / missing. A doc that exists but belongs to no fixed section is
    still included, in the catch-all appendix 'Other documents'.
    """
    repo = Path(repo)
    present = all_docs(repo)
    result = {}
    grouped = set()
    for g in PROGRAM_GROUPS:
        for d in group_docs(present, g):
            result[d] = ("included", "4 Program documentation (%s/)" % g)
            grouped.add(d)
    where = {"docs/ARCHITECTURE.md": "3 Architecture", "docs/BENCHMARKS.md": "5 Testing and benchmarks",
             "docs/REVIEW1.md": "Appendix A", "docs/REVIEW2.md": "Appendix B",
             "docs/Makefile.md": "4 Program documentation (build system)"}
    for d in present:
        if d in EXCLUDED:
            result[d] = ("excluded", EXCLUDED[d])
        elif d in where:
            result[d] = ("included", where[d])
        elif d not in grouped:
            result[d] = ("included", "Appendix C (other documents)")
    for d in expected_docs(repo) + [x for x in fixed_docs() if x != "README.md"]:
        if d not in present:
            result[d] = ("missing", "required but not found")
    if not (repo / "README.md").exists():
        result["README.md"] = ("missing", "required but not found")
    else:
        result["README.md"] = ("included", "1 Overview, 2 User guide, 6 Limitations")
        for name, reason in EXCLUDED_README_SECTIONS.items():
            result[README_SECTION_LABEL % name] = ("excluded", reason)
    return dict(sorted(result.items()))


def cmd_coverage(args):
    res = classify(REPO)
    counts = {"included": 0, "excluded": 0, "missing": 0}
    for path, (status, where) in res.items():
        counts[status] += 1
        print("%-9s %-40s %s" % (status.upper(), path, where))   # `where` is the reason for excluded
    print("\nincluded: %(included)d  excluded: %(excluded)d  missing: %(missing)d" % counts)
    return 1 if counts["missing"] else 0


# ---------------------------------------------------------------- Markdown parsing

RE_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$")
RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RE_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
RE_IMAGE = re.compile(r'^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)\s*$')
RE_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
RE_NUMBERED = re.compile(r"^\s*(\d+(\.\d+)*[.)]?)\s+(?=\S)")
RE_INLINE = re.compile(
    r"\\([\\`*_{}\[\]()#+\-.!|<>])"            # 1 backslash escape
    r"|``\s?(.+?)\s?``"                         # 2 double-backtick code span
    r"|`([^`]+)`"                               # 3 code span
    r"|\*\*(.+?)\*\*"                           # 4 bold
    r"|\[([^\]]+)\]\(([^)]+)\)"                 # 5,6 link
    r"|(?<![\w*])\*([^*\s](?:[^*]*?[^*\s])?)\*(?![\w*])"  # 7 italic
)


def parse_inline(text, bold=False, italic=False):
    """Split inline Markdown into runs: [(text, {'b','i','code'})]. Markers disappear."""
    text = re.sub(r"<br\s*/?>", " ", text)
    runs, pos = [], 0
    for m in RE_INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], {"b": bold, "i": italic, "code": False}))
        esc, code2, code1, bld, ltext, lurl, ital = m.groups()
        code = code2 if code2 is not None else code1
        if esc is not None:
            runs.append((esc, {"b": bold, "i": italic, "code": False}))
        elif code is not None:
            runs.append((code, {"b": bold, "i": italic, "code": True}))
        elif bld is not None:
            runs.extend(parse_inline(bld, True, italic))
        elif ltext is not None:
            runs.extend(parse_inline(ltext, bold, italic))
            if lurl.startswith("http") and ltext != lurl:
                runs.append((" (%s)" % lurl, {"b": bold, "i": italic, "code": False}))
        else:
            runs.extend(parse_inline(ital, bold, True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], {"b": bold, "i": italic, "code": False}))
    return runs


def runs_text(runs):
    return "".join(t for t, _ in runs)


RE_PROVENANCE = re.compile(r"\s*\(from [^()]*\)\s*$")


def strip_provenance(text):
    """'Command usage (from docs/src/commands.md)' -> 'Command usage'."""
    return RE_PROVENANCE.sub("", text)


def clean_heading(text):
    """Heading text without Markdown markers, the source's own leading number or a '(from <file>)' suffix."""
    plain = runs_text(parse_inline(text)).strip()
    plain = RE_NUMBERED.sub("", plain, count=1) if RE_NUMBERED.match(plain) else plain
    return strip_provenance(plain)


def split_row(line):
    """Split one pipe-table row into cells (a backslash-escaped pipe or a pipe inside `code` stays)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code, i = [], "", False, 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if c == "`":
            in_code = not in_code
        if c == "|" and not in_code:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += c
        i += 1
    cells.append(cur.strip())
    return cells


def is_block_start(line, nxt):
    """True if `line` begins something other than a plain paragraph line."""
    return bool(RE_FENCE.match(line) or RE_HEADING.match(line) or RE_ITEM.match(line)
                or RE_IMAGE.match(line) or RE_RULE.match(line) or line.lstrip().startswith(">")
                or ("|" in line and nxt is not None and RE_TABLE_SEP.match(nxt)))


def parse_markdown(text, base_dir, shift=0, drop_title=False):
    """Parse the Markdown subset used by this repo into blocks.

    Blocks are dicts: h (level, text), p (runs), li (level, ordered, num, runs), code (text),
    table (rows of runs, first row is the header), image (path, alt), quote (runs).
    `shift` is added to heading levels; `drop_title` removes the first level-1 heading.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = text.expandtabs(4).split("\n")
    blocks, i, n = [], 0, len(lines)
    title_dropped = not drop_title
    stack = []  # indents of open list levels
    while i < n:
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < n else None
        if not line.strip():
            i += 1
            continue
        m = RE_FENCE.match(line)
        if m:
            indent, fence = len(m.group(1)), m.group(2)
            body = []
            i += 1
            while i < n and not (lines[i].strip().startswith(fence[0] * len(fence)) and not lines[i].strip().strip(fence[0])):
                body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i].lstrip())
                i += 1
            i += 1
            blocks.append({"t": "code", "text": "\n".join(body)})
            continue
        m = RE_HEADING.match(line)
        if m:
            level = len(m.group(1))
            i += 1
            stack = []
            if level == 1 and not title_dropped:
                title_dropped = True
                continue
            blocks.append({"t": "h", "level": max(1, level + shift), "text": clean_heading(m.group(2))})
            continue
        if RE_RULE.match(line) and not RE_ITEM.match(line):
            i += 1
            continue
        m = RE_IMAGE.match(line)
        if m:
            blocks.append({"t": "image", "alt": runs_text(parse_inline(m.group(1))),
                           "path": (Path(base_dir) / m.group(2)).resolve()})
            i += 1
            continue
        if "|" in line and nxt is not None and RE_TABLE_SEP.match(nxt):
            rows = [split_row(line)]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(split_row(lines[i]))
                i += 1
            width = len(rows[0])
            rows = [(r + [""] * width)[:width] for r in rows]
            blocks.append({"t": "table", "rows": [[parse_inline(c) for c in r] for r in rows]})
            continue
        if line.lstrip().startswith(">"):
            q = []
            while i < n and lines[i].lstrip().startswith(">"):
                q.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append({"t": "quote", "runs": parse_inline(" ".join(q))})
            continue
        m = RE_ITEM.match(line)
        if m:
            indent = len(m.group(1))
            while stack and indent < stack[-1]:
                stack.pop()
            if not stack or indent > stack[-1]:
                stack.append(indent)
            ordered = m.group(2)[0].isdigit()
            body = [m.group(3).strip()]
            i += 1
            while i < n:  # continuation lines of this item
                cont = lines[i]
                if not cont.strip():
                    j = i + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and lines[j].startswith(" " * (indent + 2)) and not RE_ITEM.match(lines[j]) \
                            and not RE_FENCE.match(lines[j]):
                        i = j
                        continue
                    break
                if is_block_start(cont, lines[i + 1] if i + 1 < n else None) and not cont.startswith(" "):
                    break
                if RE_ITEM.match(cont) or RE_FENCE.match(cont):
                    break
                body.append(cont.strip())
                i += 1
            blocks.append({"t": "li", "level": min(len(stack) - 1, 2), "ordered": ordered,
                           "num": m.group(2).rstrip(".)") if ordered else "",
                           "runs": parse_inline(" ".join(body))})
            continue
        para = [line.strip()]
        i += 1
        stack = []
        while i < n and lines[i].strip() and not is_block_start(lines[i], lines[i + 1] if i + 1 < n else None):
            para.append(lines[i].strip())
            i += 1
        blocks.append({"t": "p", "runs": parse_inline(" ".join(para))})
    return blocks


def md_sections(text):
    """Split Markdown at level-2 headings: (preface lines, {heading text: lines incl. its own heading})."""
    pre, secs, cur, fenced = [], {}, None, False
    for line in text.split("\n"):
        if RE_FENCE.match(line):
            fenced = not fenced
        m = None if fenced else re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            cur = m.group(1)
            secs[cur] = [line]
        elif cur is None:
            pre.append(line)
        else:
            secs[cur].append(line)
    return "\n".join(pre), {k: "\n".join(v) for k, v in secs.items()}


def drop_excluded_sections(secs):
    """Remove the README sections listed in EXCLUDED_README_SECTIONS (matched by heading prefix)."""
    return {k: v for k, v in secs.items() if not any(k.startswith(x) for x in EXCLUDED_README_SECTIONS)}


def find_section(secs, prefix):
    for k, v in secs.items():
        if k.startswith(prefix):
            return v
    raise BuildError("section starting with %r not found" % prefix)


def first_paragraph_of(text, heading="Purpose"):
    """First paragraph under '## <heading>' as plain text (used for the test overview table)."""
    _, secs = md_sections(text)
    body = secs.get(heading)
    if not body:
        return ""
    paras = [b for b in parse_markdown(body, ".", 0) if b["t"] in ("p", "li")]
    return runs_text(paras[0]["runs"]) if paras else ""


# ---------------------------------------------------------------- document model

class Model:
    """The document as a list of blocks plus bookkeeping for verification."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.blocks = []
        self.docs = {}      # repo-relative path -> heading text that must appear in the output
        self.images = []    # image paths used

    def h(self, level, text, key=None, appendix=False):
        self.blocks.append({"t": "h", "level": level, "text": strip_provenance(text), "key": key,
                            "appendix": appendix})

    def read(self, rel):
        p = self.repo / rel
        if not p.exists():
            raise BuildError("included doc is missing: %s" % rel)
        return p.read_text(encoding="utf-8")

    def add_md(self, rel, text=None, shift=0, drop_title=False, key=None, mark=True):
        """Append the blocks of a doc (or a given piece of it); remember the marker heading."""
        if text is None:
            text = self.read(rel)
        blocks = parse_markdown(text, (self.repo / rel).parent, shift, drop_title)
        first = True
        for b in blocks:
            if b["t"] == "h":
                b.update(key=key if first else None, appendix=False)
                if first and mark:
                    self.docs.setdefault(rel, b["text"])
                first = False
            if b["t"] == "image":
                self.images.append(b["path"])
        if first and mark:
            self.docs.setdefault(rel, "")
        self.blocks.extend(blocks)
        return blocks


def build_model(repo=REPO):
    """Assemble the whole document in the required order from the repo's files."""
    repo = Path(repo)
    status = classify(repo)
    missing = [p for p, (s, _) in status.items() if s == "missing"]
    if missing:
        raise BuildError("missing docs: " + ", ".join(missing))
    present = [p for p, (s, _) in status.items() if s == "included"]
    m = Model(repo)
    readme = m.read("README.md")
    readme_pre, readme_secs = md_sections(readme)
    readme_secs = drop_excluded_sections(readme_secs)

    # 1 Overview
    m.h(1, "Overview and how to build and run")
    m.h(2, "About minibox")
    m.add_md("README.md", readme_pre, shift=1, drop_title=True, mark=False)
    for name, title in (("Prerequisites", None), ("Setup", None), ("Build", None), ("Demo", "Demo")):
        m.add_md("README.md", find_section(readme_secs, name), shift=0, mark=False)
    m.docs["README.md"] = "About minibox"

    # 2 User guide
    m.h(1, "User guide")
    m.h(2, "CLI reference")
    m.add_md("README.md", find_section(readme_secs, "Run"), shift=1, mark=False)
    m.add_md("README.md", find_section(readme_secs, "Manage containers"), shift=1, mark=False)
    m.add_md("README.md", find_section(readme_secs, "Run a command in a running"), shift=1, mark=False)
    _, cmd_secs = md_sections(m.read("docs/src/commands.md"))
    m.h(3, "Command usage and behaviour")
    m.add_md("docs/src/commands.md", find_section(cmd_secs, "Usage"), shift=2, mark=False)
    m.add_md("docs/src/commands.md", find_section(cmd_secs, "Behaviour"), shift=2, mark=False)
    m.h(3, "Environment variables")
    m.blocks.append({"t": "table", "rows": [[[("Variable", {})], [("Meaning", {})], [("Documented in", {})]]] + [
        [[(name, {"code": True})], parse_inline(meaning), [(src, {"code": True})]]
        for name, meaning, src in ENV_VARS]})
    m.h(2, "Web UI guide")
    m.add_md("README.md", find_section(readme_secs, "Web UI"), shift=1, mark=False)
    _, app_secs = md_sections(m.read("docs/ui/app.md"))
    m.add_md("docs/ui/app.md", find_section(app_secs, "Security model"), shift=1, mark=False)

    # 3 Architecture
    m.h(1, "Architecture")
    m.add_md("docs/ARCHITECTURE.md", shift=0, drop_title=True)

    # 4 Program documentation
    m.h(1, "Program documentation")
    for g in PROGRAM_GROUPS:
        docs = group_docs(present, g)
        if not docs:
            continue
        m.h(2, "%s/" % g)
        for d in docs:
            m.add_md(d, shift=2, key=d)
    m.h(2, "Build system")
    m.add_md("docs/Makefile.md", shift=2, key="docs/Makefile.md")

    # 5 Testing and benchmarks
    m.h(1, "Testing and benchmarks")
    m.h(2, "Test suite overview")
    m.blocks.append({"t": "xref", "rows": [(d, first_paragraph_of(m.read(d))) for d in group_docs(present, "tests")]})
    m.add_md("docs/BENCHMARKS.md", shift=1, key="docs/BENCHMARKS.md")

    # 6 Limitations
    m.h(1, "Limitations")
    m.add_md("README.md", find_section(readme_secs, "Known limitations"), shift=0, drop_title=False, mark=False)
    # the source heading "Known limitations" would duplicate the section title: drop it
    idx = max(i for i, b in enumerate(m.blocks) if b["t"] == "h" and b["text"] == "Known limitations")
    del m.blocks[idx]

    # Appendices
    m.h(1, "Review 1 guide", appendix=True)
    m.add_md("docs/REVIEW1.md", shift=1, drop_title=True)
    m.h(1, "Review 2 guide", appendix=True)
    m.add_md("docs/REVIEW2.md", shift=1, drop_title=True)
    other = [p for p in present if status[p][1].startswith("Appendix C")]
    if other:
        m.h(1, "Other documents", appendix=True)
        for d in other:
            m.add_md(d, shift=1)
    # the appendix guides lose their own title, so their marker is their first remaining heading
    for rel in ("docs/REVIEW1.md", "docs/REVIEW2.md", "docs/ARCHITECTURE.md"):
        m.docs[rel] = first_heading_after_drop(m, rel)
    normalize_levels(m.blocks)
    number_headings(m.blocks)
    resolve_xrefs(m.blocks)
    return m


def first_heading_after_drop(m, rel):
    """Text of the first heading that came from `rel` (set by add_md as the marker)."""
    return m.docs.get(rel, "")


def normalize_levels(blocks):
    """A heading may be at most one level deeper than the previous one."""
    prev = 0
    for b in blocks:
        if b["t"] == "h":
            b["level"] = min(b["level"], prev + 1, 9)
            prev = b["level"]


def number_headings(blocks):
    """Prefix every heading with its section number (1, 1.1, ...; Appendix A, A.1, ...)."""
    top, letters, counters, label = 0, 0, [], ""
    for b in blocks:
        if b["t"] != "h":
            continue
        lvl = b["level"]
        if lvl == 1:
            counters = []
            if b.get("appendix"):
                letters += 1
                label = chr(ord("A") + letters - 1)
                num = "Appendix " + label
            else:
                top += 1
                label = str(top)
                num = label
        else:
            del counters[lvl - 1:]          # counters[k] = count at level k+2
            while len(counters) < lvl - 2:
                counters.append(0)
            if len(counters) == lvl - 2:
                counters.append(0)
            counters[lvl - 2] += 1
            num = label + "".join("." + str(c) for c in counters)
        b["num"] = num
        b["title"] = b["text"]
        b["text"] = "%s  %s" % (num, b["text"])


def resolve_xrefs(blocks):
    """Turn the 'xref' placeholder into a table whose last column is the section number of each doc."""
    nums = {b["key"]: b["num"] for b in blocks if b["t"] == "h" and b.get("key")}
    for i, b in enumerate(blocks):
        if b["t"] != "xref":
            continue
        head = [[("File", {})], [("Purpose", {})], [("Section", {})]]
        rows = [head]
        for d, purpose in b["rows"]:
            rows.append([[(d[len("docs/"):-3], {"code": True})], parse_inline(purpose), [(nums.get(d, "?"), {})]])
        blocks[i] = {"t": "table", "rows": rows}


# ---------------------------------------------------------------- verification helpers (pure)

HTML_TAGS = ("a|abbr|b|blockquote|br|center|code|dd|details|div|dl|dt|em|font|h[1-6]|hr|i|img|kbd|li|ol|p|pre|"
             "span|strong|sub|summary|sup|table|tbody|td|th|thead|tr|u|ul")
LEFTOVER_RULES = (
    ("bold marker '**'", re.compile(r"\*\*")),
    ("heading marker '#'", re.compile(r"^\s*#{1,6}\s+\S")),
    ("table separator '|---|'", re.compile(r"\|\s*:?-{3,}|^\s*:?-{3,}:?\s*\|")),
    ("code fence", re.compile(r"```|~~~")),
    ("backtick", re.compile(r"`")),
    ("image/link syntax", re.compile(r"!?\[[^\]]*\]\([^)]*\)")),
    ("raw HTML tag", re.compile(r"</?(?:%s)(?:\s[^<>]*)?/?>" % HTML_TAGS, re.I)),
    ("list marker", re.compile(r"^\s*[-*+]\s+\S")),
    ("italic marker '*'", re.compile(r"(?<![\w*])\*[^*\s][^*]*\*(?![\w*])")),
)


def find_leftovers(line):
    """Return the Markdown leftovers found in one line of NON-code output text."""
    return [name for name, rx in LEFTOVER_RULES if rx.search(line)]


RE_HEAD_NUM = re.compile(r"^(?:Appendix )?([A-Z]|\d+)((?:\.\d+)*)\s{2}(.*)$")


def check_heading_numbers(headings):
    """headings: [(level, text)] in document order. Returns a list of problems (empty if consistent).

    Each heading must look like '2.3  Title' (or 'Appendix B  Title', 'B.1  Title'); the number must
    have `level` parts and be exactly one after its previous sibling (or .1 for a first child).
    """
    errors, last = [], []   # last = components of the previous heading's number
    for level, text in headings:
        m = RE_HEAD_NUM.match(text)
        if not m:
            errors.append("heading without a number: %r" % text)
            continue
        parts = [m.group(1)] + [p for p in m.group(2).split(".") if p]
        if len(parts) != level:
            errors.append("level %d but number has %d parts: %r" % (level, len(parts), text))
            continue
        if re.match(r"^\d+[.)]\s", m.group(3)):
            errors.append("heading number is duplicated in the title: %r" % text)
        if level == 1:
            if last:
                prev = last[0]
                ok = (prev.isdigit() and parts[0].isdigit() and int(parts[0]) == int(prev) + 1) or \
                     (prev.isdigit() and parts[0] == "A") or \
                     (prev.isalpha() and parts[0].isalpha() and ord(parts[0]) == ord(prev) + 1)
            else:
                ok = parts[0] == "1"
        else:
            if len(last) >= level:
                ok = parts[:-1] == last[:level - 1] and parts[-1].isdigit() and int(parts[-1]) == int(last[level - 1]) + 1
            elif len(last) == level - 1:
                ok = parts[:-1] == last and parts[-1] == "1"
            else:
                ok = False
        if not ok:
            errors.append("unexpected number %r after %s" % (text, ".".join(last) or "start"))
        last = parts
    return errors


# ---------------------------------------------------------------- SVG -> PNG

def flatten_svg(svg):
    """Resolve CSS variables to their light-theme values and drop the dark-mode block.

    The chart SVGs colour everything with var(--x) and a prefers-color-scheme media query; most
    converters do not evaluate those, so the colours are written out first.
    """
    svg = re.sub(r"@media[^{]*\{\s*[^{}]*\{[^}]*\}\s*\}", "", svg, flags=re.S)
    values = dict(re.findall(r"--([\w-]+)\s*:\s*([^;}]+)", svg))
    return re.sub(r"var\(--([\w-]+)\)", lambda m: values.get(m.group(1), "#000000").strip(), svg)


def svg_to_png(svg_path, png_path, route, width=1600):
    """Convert one SVG to PNG with the chosen route ('rsvg-convert' or 'cairosvg')."""
    svg = flatten_svg(Path(svg_path).read_text(encoding="utf-8"))
    flat = Path(png_path).with_suffix(".flat.svg")
    flat.write_text(svg, encoding="utf-8")
    if route == "rsvg-convert":
        r = subprocess.run(["rsvg-convert", "-w", str(width), "-o", str(png_path), str(flat)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise BuildError("rsvg-convert failed for %s: %s" % (svg_path, r.stderr.strip()))
    elif route == "cairosvg":
        import cairosvg
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(png_path), output_width=width)
    else:
        raise BuildError("no SVG converter available (run: build_deliverables.py check-tools)")
    flat.unlink()


# ---------------------------------------------------------------- table column widths (pure)

MIN_COL_PCT = 8.0      # no column narrower than this share of the text width ...
MAX_COL_PCT = 70.0     # ... or wider than this one
CHAR_TWIPS = 110       # width of one character at ~9.5 pt in a monospaced font (a safe upper bound)
CELL_PAD_TWIPS = 216   # Word's default left + right cell margins (108 + 108)


def compute_col_widths(rows, total=TEXT_WIDTH_TWIPS, min_pct=MIN_COL_PCT, max_pct=MAX_COL_PCT):
    """Column widths in twips for a table of plain-text cells (`rows` = list of lists of str).

    Each column starts proportional to its longest cell text, is clamped between `min_pct` and
    `max_pct` of `total`, and is never narrower than its longest unbreakable token (a file path, say)
    where that is feasible: the token floor is itself capped at `max_pct`, and if all floors together
    exceed `total` they are scaled down. The result always adds up to exactly `total`.
    """
    n = len(rows[0]) if rows else 0
    if n == 0:
        return []
    longest = [max(1, max(len(r[c]) for r in rows)) for c in range(n)]
    token = [max((len(t) for r in rows for t in r[c].split()), default=1) for c in range(n)]
    hi_cap = total * max_pct / 100.0
    lo = [max(total * min_pct / 100.0, min(t * CHAR_TWIPS + CELL_PAD_TWIPS, hi_cap)) for t in token]
    if sum(lo) > total:                       # not feasible: shrink every floor evenly
        lo = [x * total / sum(lo) for x in lo]
    hi = [max(hi_cap, x) for x in lo]
    if sum(hi) < total:                       # few columns: the maximum cannot hold the whole width
        hi = [h * total / sum(hi) for h in hi]
    width = [0.0] * n
    free = set(range(n))
    remaining = float(total)
    while free:                               # proportional share; pin violators at their bound, repeat
        weight = sum(longest[c] for c in free)
        share = {c: remaining * longest[c] / weight for c in free}
        under = {c: lo[c] for c in free if share[c] < lo[c]}
        over = {c: hi[c] for c in free if share[c] > hi[c]}
        # pin only the side with the larger total violation (the classic water-filling rule), so the
        # other side can still absorb or give up the difference
        if sum(lo[c] - share[c] for c in under) >= sum(share[c] - hi[c] for c in over):
            bad = under
        else:
            bad = over
        if not bad:
            for c in free:
                width[c] = share[c]
            break
        for c, w in bad.items():
            width[c] = w
            remaining -= w
            free.discard(c)
    scale = total / sum(width)                # only differs from 1 if every column was pinned
    width = [w * scale for w in width]
    ints = [int(w) for w in width]            # largest-remainder rounding so the sum is exact
    order = sorted(range(n), key=lambda c: width[c] - ints[c], reverse=True)
    for c in order[:total - sum(ints)]:
        ints[c] += 1
    return ints


# ---------------------------------------------------------------- docx rendering

def build_docx(model, cfg_values, out_dir, svg_route):
    """Render the model to <out_dir>/MiniBox_Documentation.docx. Returns (path, stats)."""
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_COLOR_INDEX
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor, Twips

    out_dir = assert_outside_repo(out_dir)
    build_dir = assert_outside_repo(out_dir / "build")
    build_dir.mkdir(parents=True, exist_ok=True)
    doc = Document()
    stats = {"headings": 0, "tables": 0, "images": 0, "code": 0}

    def set_font(style, name, size=None, color=None, bold=None):
        style.font.name = name
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(a), name)
        if size:
            style.font.size = Pt(size)
        if color:
            style.font.color.rgb = RGBColor.from_string(color)
        if bold is not None:
            style.font.bold = bold

    def shade(element_pr, fill):
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        element_pr.append(shd)

    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(PAGE_WIDTH_CM), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(MARGIN_CM)
    sec.top_margin = sec.bottom_margin = Cm(MARGIN_CM)
    text_width_cm = TEXT_WIDTH_CM

    styles = doc.styles
    set_font(styles["Normal"], "Calibri", 11)
    styles["Normal"].paragraph_format.space_after = Pt(6)
    for lvl, size in ((1, 18), (2, 15), (3, 13), (4, 12), (5, 11), (6, 11), (7, 11), (8, 11), (9, 11)):
        set_font(styles["Heading %d" % lvl], "Calibri", size, "1F3864", True)
        styles["Heading %d" % lvl].paragraph_format.keep_with_next = True
    set_font(styles["Title"], "Calibri", 28, "1F3864", True)
    code = styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
    code.base_style = styles["Normal"]
    set_font(code, "Courier New", 8.5)
    shade(code.element.get_or_add_pPr(), "F3F4F6")   # before spacing/indent: schema order in pPr
    code.paragraph_format.space_after = Pt(6)
    code.paragraph_format.left_indent = Cm(0.2)
    code.paragraph_format.right_indent = Cm(0.2)
    inline = styles.add_style("Inline Code", WD_STYLE_TYPE.CHARACTER)
    set_font(inline, "Courier New", 9.5)
    shade(inline.element.get_or_add_rPr(), "EEF0F2")
    tt = styles.add_style("Table Text", WD_STYLE_TYPE.PARAGRAPH)
    tt.base_style = styles["Normal"]
    set_font(tt, "Calibri", 9)         # table text: 9 pt (inline code in tables is 9.5 pt)
    tt.paragraph_format.space_after = Pt(0)
    num = styles.add_style("Numbered Item", WD_STYLE_TYPE.PARAGRAPH)
    num.base_style = styles["Normal"]
    num.paragraph_format.left_indent = Cm(1.0)
    num.paragraph_format.first_line_indent = Cm(-0.6)
    num.paragraph_format.space_after = Pt(3)
    for lvl in (1, 2, 3):
        s = styles.add_style("TOC %d" % lvl, WD_STYLE_TYPE.PARAGRAPH)
        s.base_style = styles["Normal"]
        s.paragraph_format.left_indent = Cm(0.6 * (lvl - 1))
        s.paragraph_format.space_after = Pt(2)
        s.font.bold = lvl == 1
    for name in ("List Bullet", "List Bullet 2", "List Bullet 3"):
        styles[name].paragraph_format.space_after = Pt(3)

    def add_runs(par, runs, size=None, force_bold=False, highlight=False):
        for text, fmt in runs:
            r = par.add_run(text)
            if fmt.get("code"):
                r.style = styles["Inline Code"]
            r.bold = True if (fmt.get("b") or force_bold) else None
            r.italic = True if fmt.get("i") else None
            if size and not fmt.get("code"):
                r.font.size = Pt(size)
            if highlight:
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW

    def field_run(par, instr):
        """Insert a complex field (PAGE etc.) into a paragraph."""
        for kind, extra in (("begin", None), (None, instr), ("separate", None), ("text", "1"), ("end", None)):
            r = par.add_run()
            if kind in ("begin", "separate", "end"):
                f = OxmlElement("w:fldChar")
                f.set(qn("w:fldCharType"), kind)
                r._r.append(f)
            elif kind is None:
                t = OxmlElement("w:instrText")
                t.set(qn("xml:space"), "preserve")
                t.text = extra
                r._r.append(t)
            else:
                r.text = extra

    # header / footer (not on the title page)
    sec.different_first_page_header_footer = True
    hp = sec.header.paragraphs[0]
    hp.text = cfg_values["project_title"]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for r in hp.runs:
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.add_run("Page ")
    field_run(fp, " PAGE ")

    # title page
    for _ in range(5):
        doc.add_paragraph()
    doc.add_paragraph(cfg_values["project_title"], style="Title").alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rr = sub.add_run("Project Documentation")
    rr.font.size = Pt(18)
    for _ in range(3):
        doc.add_paragraph()
    for label, key in (("Student", "student_name"), ("Register number", "register_no"), ("Course", "course"),
                       ("Faculty", "faculty_name"), ("Institute", "institute"), ("Team number", "team_number"),
                       ("Date", "date")):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(label + ": ").bold = True
        value = cfg_values[key]
        r = p.add_run(value)
        if value == PLACEHOLDERS[key]:
            r.font.highlight_color = WD_COLOR_INDEX.YELLOW
    doc.add_page_break()

    # table of contents: a real TOC field, pre-filled with the headings (levels 1-3)
    toc_h = doc.add_paragraph("Table of Contents", style="TOC Heading")
    toc_entries = [(b["level"], b["text"]) for b in model.blocks if b["t"] == "h" and b["level"] <= 3]
    first_par = last_par = None
    for idx, (lvl, text) in enumerate(toc_entries):
        p = doc.add_paragraph(style="TOC %d" % lvl)
        if idx == 0:
            first_par = p
        last_par = p
        p.add_run(text)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin.set(qn("w:dirty"), "true")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    if first_par is not None:
        r0 = first_par.runs[0]._r
        for el in (begin, instr, sep):
            r0.addprevious(_wrap_run(el))
        last_par._p.append(_wrap_run(end))
    doc.add_page_break()
    del toc_h

    # body
    tmp_png = {}
    for b in model.blocks:
        t = b["t"]
        if t == "h":
            doc.add_heading(b["text"], level=b["level"])
            stats["headings"] += 1
        elif t == "p":
            add_runs(doc.add_paragraph(), b["runs"])
        elif t == "quote":
            p = doc.add_paragraph(style="Quote")
            add_runs(p, b["runs"])
        elif t == "li":
            if b["ordered"]:
                p = doc.add_paragraph(style="Numbered Item")
                p.paragraph_format.left_indent = Cm(1.0 + 0.6 * b["level"])
                p.add_run(b["num"] + ".\t")
                add_runs(p, b["runs"])
            else:
                p = doc.add_paragraph(style=["List Bullet", "List Bullet 2", "List Bullet 3"][b["level"]])
                add_runs(p, b["runs"])
        elif t == "code":
            p = doc.add_paragraph(style="Code Block")
            p.add_run(b["text"].rstrip("\n"))
            stats["code"] += 1
        elif t == "table":
            stats["tables"] += 1
            rows = b["rows"]
            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = styles["Table Grid"]
            table.autofit = False          # w:tblLayout fixed: Word uses our widths, not the content
            widths = compute_col_widths([[runs_text(c) for c in r] for r in rows])
            tblpr = table._tbl.tblPr
            w = tblpr.find(qn("w:tblW"))   # edit in place: element order inside tblPr matters to Word
            if w is None:
                w = OxmlElement("w:tblW")
                tblpr.insert(1, w)
            w.set(qn("w:type"), "dxa")
            w.set(qn("w:w"), str(sum(widths)))
            for ci, cw in enumerate(widths):
                table.columns[ci].width = Twips(cw)     # w:gridCol
            for ri, row in enumerate(rows):
                if ri == 0:                # header row repeats on every page; rows may break across pages
                    table.rows[ri]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
                for ci, runs in enumerate(row):
                    cell = table.cell(ri, ci)
                    cell.width = Twips(widths[ci])
                    par = cell.paragraphs[0]
                    par.style = styles["Table Text"]
                    add_runs(par, runs, force_bold=(ri == 0))
                    if ri == 0:
                        shade(cell._tc.get_or_add_tcPr(), "D9E2F3")
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif t == "image":
            path = b["path"]
            if not path.exists():
                raise BuildError("image not found: %s" % path)
            if path.suffix.lower() == ".svg":
                png = build_dir / ("%s__%s.png" % (path.parent.name, path.stem))
                if str(path) not in tmp_png:
                    svg_to_png(path, png, svg_route)
                    tmp_png[str(path)] = png
                path = tmp_png[str(path)]
            doc.add_picture(str(path), width=Cm(min(text_width_cm, 15.5)))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if b["alt"]:
                cap = doc.add_paragraph(b["alt"], style="Caption")
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            stats["images"] += 1

    cp = doc.core_properties
    cp.title = cfg_values["project_title"]
    cp.author = cfg_values["student_name"]
    target = assert_outside_repo(out_dir / DOCX_NAME)
    doc.save(str(target))
    return target, stats


def _wrap_run(child):
    """Wrap a w:fldChar / w:instrText element in a w:r so it can sit between runs."""
    from docx.oxml import OxmlElement
    r = OxmlElement("w:r")
    r.append(child)
    return r


# ---------------------------------------------------------------- verification of the output

def docx_text_parts(path):
    """Return (headings [(level, text)], plain lines outside code, all text) from a built docx."""
    from docx import Document
    from docx.oxml.ns import qn
    d = Document(str(path))
    headings, lines, everything = [], [], []

    def para_lines(p):
        if p.style.name == "Code Block":
            everything.append(p.text)
            return
        # inline code is masked (not removed) so the text around it keeps its shape
        text = "".join("X" if (r.style is not None and r.style.name == "Inline Code") else r.text for r in p.runs)
        everything.append(p.text)
        if p.style.name.startswith("TOC"):
            return
        lines.append(text)

    for p in d.paragraphs:
        m = re.match(r"Heading (\d)$", p.style.name)
        if m:
            headings.append((int(m.group(1)), p.text))
        para_lines(p)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    para_lines(p)
    has_toc = "TOC \\o" in d.element.xml
    return headings, lines, "\n".join(everything), has_toc


def docx_tables(path):
    """Every table of a built docx in order: [{'heading': nearest heading above, 'rows': [[cell text]]}]."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = Document(str(path))
    out, heading, context = [], "", ""
    for el in d.element.body.iterchildren():
        if el.tag.endswith("}p"):
            p = Paragraph(el, d)
            m = re.match(r"Heading (\d)$", p.style.name)
            if m:
                heading = p.text
                if int(m.group(1)) <= 3:
                    context = p.text       # the document or section the table belongs to
        elif el.tag.endswith("}tbl"):
            t = Table(el, d)
            out.append({"heading": heading, "context": context, "rows": [[c.text for c in r.cells] for r in t.rows]})
    return out


def expected_test_rows(repo=REPO):
    """Rows the test overview must have: 'tests/<name>' for every tests/*.sh and tests/test_*.py
    whose doc is not excluded from the Word file."""
    tests = Path(repo) / "tests"
    names = sorted({"tests/" + f.stem for pat in ("*.sh", "test_*.py") for f in tests.glob(pat)})
    return [n for n in names if "docs/%s.md" % n not in EXCLUDED]


def missing_test_rows(table_rows, expected):
    """Names in `expected` that have no row in the overview table (first column, header skipped)."""
    have = {r[0].strip() for r in table_rows[1:]}
    return [n for n in expected if n not in have]


def largest_tables(tables, count=5):
    """[(label, data rows)] of the `count` tables with most rows (header row not counted)."""
    sized = [((t["context"] if t["context"] == t["heading"] else "%s > %s" % (t["context"], t["heading"])),
              len(t["rows"]) - 1) for t in tables]
    return sorted(sized, key=lambda x: -x[1])[:count]


def check_table_layout(path):
    """Problems with table layout in the file: not fixed, widths not the text width, header not
    repeated, rows that may not break across pages."""
    from docx import Document
    from docx.oxml.ns import qn
    problems = []
    for i, t in enumerate(Document(str(path)).tables, 1):
        tbl = t._tbl
        layout = tbl.tblPr.find(qn("w:tblLayout"))
        if layout is None or layout.get(qn("w:type")) != "fixed":
            problems.append("table %d: layout is not fixed" % i)
        grid = sum(int(g.get(qn("w:w"))) for g in tbl.tblGrid.findall(qn("w:gridCol")))
        if abs(grid - TEXT_WIDTH_TWIPS) > 1:
            problems.append("table %d: column widths add up to %d twips, text width is %d" % (i, grid, TEXT_WIDTH_TWIPS))
        if tbl.tr_lst[0].find(qn("w:trPr") + "/" + qn("w:tblHeader")) is None:
            problems.append("table %d: header row is not repeated" % i)
        if tbl.findall(".//" + qn("w:cantSplit")):
            problems.append("table %d: rows are not allowed to break across pages" % i)
    return problems


def verify_docx(path, model, repo=REPO):
    """Fail loudly (BuildError) on Markdown leftovers, missing docs, bad heading numbers, bad tables."""
    headings, lines, _, has_toc = docx_text_parts(path)
    problems = []
    problems += check_table_layout(path)
    for _, text in headings:
        if RE_PROVENANCE.search(text):
            problems.append("heading still has a provenance suffix: %r" % text)
    overview = [t for t in docx_tables(path) if t["heading"].endswith("Test suite overview")]
    if len(overview) != 1:
        problems.append("expected exactly one 'Test suite overview' table, found %d" % len(overview))
    else:
        gone = missing_test_rows(overview[0]["rows"], expected_test_rows(repo))
        if gone:
            problems.append("test suite overview is missing %d row(s): %s" % (len(gone), ", ".join(gone)))
    for line in lines:
        for why in find_leftovers(line):
            problems.append("Markdown leftover (%s): %r" % (why, line[:100]))
    problems += check_heading_numbers(headings)
    titles = {re.sub(r"^(?:Appendix )?(?:[A-Z]|\d+)(?:\.\d+)*\s{2}", "", t) for _, t in headings}
    for rel, marker in sorted(model.docs.items()):
        if marker and marker not in titles:
            problems.append("included doc not found in output: %s (expected heading %r)" % (rel, marker))
    if not has_toc:
        problems.append("no TOC field in the document")
    for _, text in headings:
        if any(text.endswith("  " + x) for x in EXCLUDED_README_SECTIONS):
            problems.append("excluded README section is in the output: %r" % text)
    if problems:
        shown = problems[:40]
        raise BuildError("verification FAILED (%d problems):\n  - %s%s" % (
            len(problems), "\n  - ".join(shown), "\n  ... and %d more" % (len(problems) - 40) if len(problems) > 40 else ""))
    return len(headings)


def render_pdf_check(docx_path, build_dir):
    """If LibreOffice exists, render the docx to PDF and return the page count (else None)."""
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        return None
    build_dir = assert_outside_repo(build_dir)
    r = subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", str(build_dir), str(docx_path)],
                       capture_output=True, text=True, timeout=300)
    pdf = build_dir / (Path(docx_path).stem + ".pdf")
    if r.returncode != 0 or not pdf.exists():
        raise BuildError("LibreOffice could not render the docx to PDF: %s" % (r.stderr.strip() or r.stdout.strip()))
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


# ---------------------------------------------------------------- docs subcommand

def cmd_docs(args):
    out = assert_outside_repo(args.out)
    modules = probe_modules(out)
    tools = {t: shutil.which(t) for t in TOOL_NAMES}
    routes = choose_routes(tools, modules)
    if not importlib.util.find_spec("docx"):
        vpy = venv_python(out)
        if modules.get("docx") == "venv" and vpy.exists():
            # Re-run under the venv's python BEFORE printing anything, so notes and warnings
            # (config created, empty values) appear once, not once per process.
            os.execv(str(vpy), [str(vpy), str(Path(__file__).resolve())] + sys.argv[1:])
        raise BuildError("python-docx is missing. Run:\n  " + "\n  ".join(venv_commands(out)))
    cfg = load_config(out)
    shown, empty = config_values(cfg)
    if empty:
        print("WARNING: empty config values shown as placeholders on the title page: %s" % ", ".join(empty))
        print("         edit %s and rebuild" % (out / "config.json"))
    if not routes["svg_to_png"]:
        raise BuildError("no SVG converter. Run one of:\n  sudo apt install librsvg2-bin\n  %s/venv/bin/pip install %s"
                         % (out, " ".join(PIP_SVG)))
    model = build_model(REPO)
    path, stats = build_docx(model, shown, out, routes["svg_to_png"])
    n = verify_docx(path, model, REPO)
    print("built %s (%d KB)" % (path, path.stat().st_size // 1024))
    print("  headings %d, tables %d, code blocks %d, images %d; docs included %d"
          % (n, stats["tables"], stats["code"], stats["images"], len(model.docs)))
    print("  verification: OK (no Markdown leftovers, all docs present, heading numbers consistent, TOC field present,")
    print("                table layout fixed, test overview has a row for every test script)")
    print("  largest tables (data rows, header not counted):")
    for heading, rows in largest_tables(docx_tables(path)):
        print("    %4d  %s" % (rows, heading))
    pages = render_pdf_check(path, out / "build")
    if pages is None:
        print("  LibreOffice not found: PDF render check skipped (sudo apt install libreoffice-writer to enable)")
    else:
        print("  LibreOffice rendered the docx to PDF: %d pages" % pages)
    return 0


# ---------------------------------------------------------------- PDF report (Part 1)

REPORT_PDF = "MiniBox_Report.pdf"
REPORT_REFERENCES = "references.md"
REPORT_ABSTRACT = "00_abstract.md"
REPORT_CHAPTERS = ("01_introduction.md", "02_background.md", "03_design.md", "04_implementation.md",
                   "05_ui_security.md", "06_testing.md", "07_evaluation.md", "08_os_concepts.md",
                   "09_limitations.md", "10_conclusion.md")
REPORT_APPENDICES = ("appendix_a_reproduce.md",)
REPORT_PAGES = (18, 22)             # allowed page count of the whole PDF
BENCH_BEFORE, BENCH_AFTER = "reference-before-clone3", "reference"    # result folders used by {{gen:...}}
PROBE_FILE = "analysis/attach_stall_probe.txt"
# Left out of the repository tree in Appendix B (build outputs, caches, third-party code, empty markers).
TREE_SKIP_DIRS = {"venv", "__pycache__", "results", ".cache", "build", "rootfs"}
TREE_SKIP_FILES = {".gitkeep", ".gitignore", "minibox", "LICENSE", "CLAUDE.md"}
TREE_GROUPS = (".", "include", "src", "scripts", "tests", "ui", "bench", "docs")
ABSTRACT_WORDS = (150, 250)
REPORT_FONTS = ("Liberation Serif", "DejaVu Sans Mono", "Liberation Sans")
MAX_HEADING_LEVEL = 3
REPORT_SHORT_TITLE_MAX = 60
WIDE_FIGURE_UNITS = 700             # an SVG wider than this (the benchmark charts) gets a landscape page, so its text stays legible
WIDE_CODE_CHARS = 85                # a code block with a longer line is printed in a smaller font

RE_FIG_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)\{#fig:([\w-]+)\}\s*$")
RE_TAB_LINE = re.compile(r"^Table:\s+(.*?)\s*\{#tab:([\w-]+)\}\s*$")
RE_SENTINEL = re.compile(r"^@@CAP(\d+)@@$")
RE_HEAD_NAME = re.compile(r"\s*\{#sec:([\w-]+)\}\s*$")
RE_CITE = re.compile(r"\[ref:([\w-]+)\]")
RE_PLACEHOLDER = re.compile(r"\b(?:TODO|FIXME|TBD|XXX)\b|\?\?\?")
RE_UNRESOLVED = re.compile(r"\{(?:fig|tab|sec):|\[ref:|@@CAP|\{\{gen:")
RE_GEN = re.compile(r"^\{\{gen:([\w:./-]+)\}\}\s*$")


def font_available(name):
    """True/False if `fc-list` knows the font family, None if fc-list does not exist."""
    if not shutil.which("fc-list"):
        return None
    r = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
    return any(name == fam.strip() for line in r.stdout.splitlines() for fam in line.split(","))


def count_words(text):
    return len(re.findall(r"[\w'-]+", text))


def parse_references(text):
    """references.md: one 'key | text' per line ('#' lines and blank lines ignored) -> {key: text}."""
    refs = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, body = line.partition("|")
        key, body = key.strip(), body.strip()
        if not sep or not re.fullmatch(r"[\w-]+", key) or not body:
            raise BuildError("%s line %d: expected 'key | reference text', got %r" % (REPORT_REFERENCES, n, line))
        if key in refs:
            raise BuildError("%s line %d: duplicate reference key %r" % (REPORT_REFERENCES, n, key))
        refs[key] = body
    return refs


# ------------------------------------------------------------ generated content ({{gen:NAME}})

def load_bench_tables(repo=REPO):
    """The module bench/make_report_tables.py, loaded from the repo (its functions make every result table)."""
    path = Path(repo) / "bench" / "make_report_tables.py"
    spec = importlib.util.spec_from_file_location("make_report_tables", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def first_sentence(text):
    """The first sentence of `text` (up to the first full stop followed by a space)."""
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"(?<=[.!?])\s", text)
    return text[:m.start()] if m else text


def purpose_of(rel, repo=REPO):
    """One-line purpose of a repo file, from its doc file docs/<path without extension>.md.

    A file directly in docs/ gives its title; any other Markdown file without a doc uses its own first paragraph. Returns '' if there is nothing to take."""
    repo = Path(repo)
    rel_p = Path(rel)
    if rel_p.parent == Path("docs") and rel_p.suffix == ".md" and (repo / rel_p).exists():
        m = re.search(r"^#\s+(.+?)\s*$", (repo / rel_p).read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else ""            # a top-level document: its title
    doc = repo / "docs" / rel_p.parent / (rel_p.stem + ".md")
    if not doc.exists() and rel_p.suffix == ".md":
        doc = repo / rel_p                         # a Markdown file describes itself
    if not doc.exists():
        return ""
    text = doc.read_text(encoding="utf-8")
    got = first_paragraph_of(text, "Purpose")
    if not got:
        paras = [b for b in parse_markdown(text, ".", 0, drop_title=True) if b["t"] == "p"]
        got = runs_text(paras[0]["runs"]) if paras else ""
    return first_sentence(got)


def tree_files(group, repo=REPO):
    """Sorted repo-relative paths of one Appendix B group ('.' = the top-level files, 'docs' = docs/*.md only)."""
    repo = Path(repo)
    if group not in TREE_GROUPS:
        raise BuildError("unknown tree group %r (known: %s)" % (group, ", ".join(TREE_GROUPS)))
    out = []
    if group in (".", "docs"):
        base = repo if group == "." else repo / "docs"
        out = [p for p in base.iterdir() if p.is_file()]
    else:
        for dirpath, dirnames, filenames in os.walk(repo / group):
            dirnames[:] = [d for d in dirnames if d not in TREE_SKIP_DIRS]
            out += [Path(dirpath) / f for f in filenames]
    rels = sorted(p.relative_to(repo).as_posix() for p in out
                  if p.name not in TREE_SKIP_FILES and p.suffix not in (".pyc", ".o", ".d"))
    return rels


def tree_table(group, repo=REPO):
    """Markdown table 'File | Purpose' for one group of the repository tree. Files with no purpose to
    take from the docs are not listed (the build prints them, see `tree_omitted`)."""
    rows = []
    for rel in tree_files(group, repo):
        purpose = purpose_of(rel, repo)
        if purpose:
            rows.append("| `%s` | %s |" % (rel, purpose.replace("|", "\\|")))
    if not rows:
        raise BuildError("tree group %r has no files with a documented purpose" % group)
    return "| File | Purpose |\n|---|---|\n" + "\n".join(rows)


def tree_omitted(repo=REPO):
    """Files that appear in no Appendix B table because the docs give no purpose for them."""
    return [rel for g in TREE_GROUPS for rel in tree_files(g, repo) if not purpose_of(rel, repo)]


def generate_block(name, repo=REPO):
    """Markdown for one {{gen:NAME}} directive. Tables come from the functions of bench/make_report_tables.py
    applied to the result folders of the repo, so no number in a report table is typed by hand."""
    repo = Path(repo)
    tables = load_bench_tables(repo)
    results = repo / "bench" / "results"
    after, before = results / BENCH_AFTER, results / BENCH_BEFORE
    kind, _, arg = name.partition(":")

    def rows(folder, fname):
        path = folder / fname
        if not path.exists():
            raise BuildError("{{gen:%s}}: %s does not exist" % (name, path))
        return tables.read_csv(str(path))

    if name == "startup":
        return tables.startup_table(rows(after, "startup_summary.csv"))
    if name == "compare":
        return tables.compare_table(rows(before, "startup_summary.csv"), rows(after, "startup_summary.csv"))
    if name == "memory":
        return tables.memory_table(rows(after, "memory.csv"))
    if name == "incremental":
        return tables.incremental_table(rows(after, "memory_incremental.csv"))
    if name == "limits":
        return tables.limits_table(rows(after, "limits.csv"))
    if name == "limits-before":
        return tables.limits_table(rows(before, "limits.csv"))
    if name == "machine":
        if not (after / "system.json").exists():
            raise BuildError("{{gen:machine}}: %s/system.json does not exist" % after)
        return tables.machine_lines(tables.load_system(str(after)))
    if name == "probe":
        path = results / PROBE_FILE
        if not path.exists():
            raise BuildError("{{gen:probe}}: %s does not exist" % path)
        return "```\n" + path.read_text(encoding="utf-8").rstrip("\n") + "\n```"
    if kind == "tree" and arg:
        return tree_table(arg, repo)
    raise BuildError("unknown directive {{gen:%s}}" % name)


def expand_generated(text, repo=REPO):
    """Replace every line `{{gen:NAME}}` outside code fences by its generated Markdown."""
    out, fenced = [], False
    for line in text.split("\n"):
        if RE_FENCE.match(line):
            fenced = not fenced
        m = None if fenced else RE_GEN.match(line.strip())
        out.append(generate_block(m.group(1), repo) if m else line)
    return "\n".join(out)



def preprocess_report_md(text):
    """Replace figure lines and table caption lines by placeholder paragraphs.

    Returns (new text, captions) where captions[i] = {'kind': 'fig'|'tab', 'name', 'caption', 'path'}.
    `![Caption](figures/x.svg){#fig:name}` and `Table: Caption {#tab:name}` are the report's own syntax.
    """
    caps, out, fenced = [], [], False
    for line in text.split("\n"):
        if RE_FENCE.match(line):
            fenced = not fenced
        m = None if fenced else RE_FIG_LINE.match(line.strip())
        t = None if (fenced or m) else RE_TAB_LINE.match(line.strip())
        if m:
            caps.append({"kind": "fig", "caption": m.group(1), "path": m.group(2), "name": m.group(3)})
        elif t:
            caps.append({"kind": "tab", "caption": t.group(1), "name": t.group(2), "path": None})
        else:
            out.append(line)
            continue
        out += ["", "@@CAP%d@@" % (len(caps) - 1), ""]
    return "\n".join(out), caps


def parse_report_file(name, text, base_dir):
    """Blocks of one report file. Figure and caption placeholders become 'figure' blocks and a
    'caption' that is attached to the table that follows it (BuildError if there is none)."""
    text, caps = preprocess_report_md(text)
    raw = parse_markdown(text, base_dir)
    blocks, pending = [], None
    for b in raw:
        m = RE_SENTINEL.match(runs_text(b["runs"])) if b["t"] == "p" else None
        if m:
            cap = caps[int(m.group(1))]
            if pending:
                raise BuildError("%s: table caption {#tab:%s} is not followed by a table" % (name, pending["name"]))
            if cap["kind"] == "fig":
                blocks.append({"t": "figure", "name": cap["name"], "caption": cap["caption"], "path": cap["path"]})
            else:
                pending = cap
            continue
        if b["t"] == "h":
            hm = RE_HEAD_NAME.search(b["text"])
            if hm:
                b["name"], b["text"] = hm.group(1), b["text"][:hm.start()]
        if pending:
            if b["t"] != "table":
                raise BuildError("%s: table caption {#tab:%s} is not followed by a table" % (name, pending["name"]))
            b["name"], b["caption"] = pending["name"], pending["caption"]
            pending = None
        blocks.append(b)
    if pending:
        raise BuildError("%s: table caption {#tab:%s} is not followed by a table" % (name, pending["name"]))
    return blocks


def number_report(chapters):
    """Number headings (1, 1.1, 1.1.1) and captions (Figure 3.2, Table 3.1) in place.

    A heading may end in `{#sec:name}` so that text can refer to it as {sec:name} ("Section 4.3").

    `chapters` = [{'file', 'kind': 'abstract'|'chapter'|'appendix', 'blocks'}]. Chapters are numbered
    1, 2, ...; appendices are lettered A, B, ... ("Appendix A", sections A.1, "Table A.1"). A caption
    in the abstract is an error. Returns {'fig:name': (label, anchor), ...}.
    """
    labels, top, counters, count, appendices = {}, "", [], 0, 0
    for ch in chapters:
        figs = tabs = 0
        for b in ch["blocks"]:
            if b["t"] == "h":
                if b["level"] > MAX_HEADING_LEVEL:
                    raise BuildError("%s: heading deeper than %d levels: %r" % (ch["file"], MAX_HEADING_LEVEL, b["text"]))
                if ch["kind"] == "abstract":
                    b["num"], b["id"] = "", "sec-" + re.sub(r"\W+", "-", b["text"].lower()).strip("-")
                    continue
                lvl = b["level"]
                if lvl == 1:
                    if ch["kind"] == "appendix":
                        appendices += 1
                        top = chr(ord("A") + appendices - 1)
                        b["num"] = "Appendix " + top
                    else:
                        count += 1
                        top = str(count)
                        b["num"] = top
                    counters = []
                    figs = tabs = 0
                else:
                    idx = lvl - 2                      # counters[k] counts the headings of level k + 2
                    if not top or idx > len(counters):
                        raise BuildError("%s: heading %r skips a level" % (ch["file"], b["text"]))
                    del counters[idx + 1:]
                    if idx == len(counters):
                        counters.append(0)
                    counters[idx] += 1
                    b["num"] = ".".join([top] + [str(c) for c in counters[:idx + 1]])
                b["id"] = "sec-" + b["num"].replace(".", "-").replace("Appendix ", "")
                if b.get("name"):
                    key = "sec:%s" % b["name"]
                    if key in labels:
                        raise BuildError("%s: duplicate name {#%s}" % (ch["file"], key))
                    labels[key] = ("Section " + b["num"] if lvl > 1 or ch["kind"] != "appendix" else b["num"], b["id"])
            elif b["t"] in ("figure", "table") and b.get("name"):
                if ch["kind"] == "abstract" or not top:
                    raise BuildError("%s: figures and tables are only allowed inside numbered chapters" % ch["file"])
                if b["t"] == "figure":
                    figs += 1
                    kind, label = "fig", "Figure %s.%d" % (top, figs)
                else:
                    tabs += 1
                    kind, label = "tab", "Table %s.%d" % (top, tabs)
                key = "%s:%s" % (kind, b["name"])
                if key in labels:
                    raise BuildError("%s: duplicate name {#%s}" % (ch["file"], key))
                b["label"], b["id"] = label, "%s-%s" % (kind, b["name"])
                labels[key] = (label, b["id"])
    return labels


class Resolver:
    """Turns inline runs into HTML and resolves {fig:}, {tab:} and [ref:] (collecting every problem)."""

    def __init__(self, labels, refs):
        self.labels, self.refs = labels, refs
        self.cited = []                 # reference keys in order of first citation
        self.used = set()               # figure/table keys that were referred to
        self.errors = []

    def cite_number(self, key):
        if key not in self.refs:
            self.errors.append("citation [ref:%s] has no entry in %s" % (key, REPORT_REFERENCES))
            return None
        if key not in self.cited:
            self.cited.append(key)
        return self.cited.index(key) + 1

    def text(self, raw):
        """HTML for one plain-text run with its cross-references and citations resolved."""
        out, pos = [], 0
        for m in re.finditer(r"\{(fig|tab|sec):([\w-]+)\}|\[ref:([\w-]+)\]", raw):
            out.append(htmllib.escape(raw[pos:m.start()]))
            pos = m.end()
            if m.group(3):
                n = self.cite_number(m.group(3))
                out.append("[?]" if n is None else '<a class="cite" href="#ref-%s">[%d]</a>' % (m.group(3), n))
            else:
                key = "%s:%s" % (m.group(1), m.group(2))
                if key not in self.labels:
                    self.errors.append("unresolved reference {%s}" % key)
                    out.append("{%s}" % key)
                else:
                    self.used.add(key)
                    label, anchor = self.labels[key]
                    out.append('<a class="xref" href="#%s">%s</a>' % (anchor, label))
        out.append(htmllib.escape(raw[pos:]))
        return "".join(out)

    def runs(self, runs):
        out = []
        for text, fmt in runs:
            h = '<code>%s</code>' % htmllib.escape(text) if fmt.get("code") else self.text(text)
            if fmt.get("b"):
                h = "<strong>%s</strong>" % h
            if fmt.get("i"):
                h = "<em>%s</em>" % h
            out.append(h)
        return "".join(out)


def svg_width(path):
    """Width (user units) of the viewBox of an SVG file, 0 if it has none (a raster image, say)."""
    m = re.search(r'viewBox="\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)', Path(path).read_text(encoding="utf-8", errors="ignore")[:2000]) \
        if Path(path).suffix == ".svg" else None
    return float(m.group(1)) if m else 0.0


def blocks_html(blocks, res, rdir):
    """HTML of a list of blocks (lists are grouped into <ul>/<ol>)."""
    out, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        t = b["t"]
        if t == "li":
            j = i
            while j < len(blocks) and blocks[j]["t"] == "li":
                j += 1
            group = blocks[i:j]
            tag = "ol" if group[0]["ordered"] else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join('<li class="l%d">%s</li>' % (g["level"], res.runs(g["runs"]))
                                                     for g in group), tag))
            i = j
            continue
        i += 1
        if t == "h":
            tag = "h%d" % b["level"]
            cls = ' class="chapter"' if b["level"] == 1 else ""
            num = '<span class="num">%s</span> ' % b["num"] if b.get("num") else ""
            out.append('<%s id="%s"%s>%s%s</%s>' % (tag, b["id"], cls, num, htmllib.escape(b["text"]), tag))
        elif t == "p":
            out.append("<p>%s</p>" % res.runs(b["runs"]))
        elif t == "quote":
            out.append("<blockquote>%s</blockquote>" % res.runs(b["runs"]))
        elif t == "code":
            wide = ' class="wide"' if max((len(l) for l in b["text"].split("\n")), default=0) > WIDE_CODE_CHARS else ""
            out.append("<pre%s><code>%s</code></pre>" % (wide, htmllib.escape(b["text"].rstrip("\n"))))
        elif t == "figure":
            path = (Path(rdir) / b["path"]).resolve()
            if not path.exists():
                raise BuildError("figure file not found: %s (run scripts/make_report_diagrams.py)" % path)
            wide = ' class="wide"' if svg_width(path) > WIDE_FIGURE_UNITS else ""
            out.append('<figure id="%s"%s><img src="%s" alt="%s"><figcaption><strong>%s:</strong> %s</figcaption></figure>'
                       % (b["id"], wide, htmllib.escape(b["path"]), htmllib.escape(b["caption"]), b["label"],
                          res.runs(parse_inline(b["caption"]))))
        elif t == "table":
            rows = b["rows"]
            head = "".join("<th>%s</th>" % res.runs(c) for c in rows[0])
            body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % res.runs(c) for c in r) for r in rows[1:])
            cap = ""
            if b.get("name"):
                cap = '<p class="caption" id="%s"><strong>%s:</strong> %s</p>' % (
                    b["id"], b["label"], res.runs(parse_inline(b["caption"])))
            out.append('<div class="tablebox">%s<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
                       % (cap, head, body))
    return "\n".join(out)


def toc_entries(chapters, kind=None):
    """[(level, number, title, anchor)] for the contents page: every numbered heading of the files of
    `kind` ('chapter' or 'appendix'; None = both). The References entry is added by the caller."""
    out = []
    for ch in chapters:
        if ch["kind"] == "abstract" or (kind and ch["kind"] != kind):
            continue
        for b in ch["blocks"]:
            if b["t"] == "h" and b["level"] <= MAX_HEADING_LEVEL:
                out.append((b["level"], b.get("num", ""), b["text"], b["id"]))
    return out


def css_string(text):
    """A CSS string literal for `text`."""
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


REPORT_CSS = """
@page { size: A4; margin: 2.5cm;
  @top-right { content: %(header)s; font: 9pt "Liberation Sans", sans-serif; color: #444; }
  @bottom-center { content: "Page " counter(page) " of " counter(pages); font: 9pt "Liberation Sans", sans-serif; color: #444; } }
@page wide { size: A4 landscape; }
@page :first { @top-right { content: none; } @bottom-center { content: none; } }
html { font: 11pt/1.27 "Liberation Serif", serif; color: #111; }
body { margin: 0; text-align: justify; hyphens: auto; }
h1, h2, h3, .toc-title, .abstract-title { font-family: "Liberation Sans", sans-serif; color: #000; text-align: left; hyphens: none; }
h1 { font-size: 19pt; margin: 0 0 0.8em; break-after: avoid; }
h1.chapter { margin-top: 1.8em; }
.toc + section h1.chapter, .toc + section > h1.chapter:first-child { margin-top: 0; break-before: page; }
h2 { font-size: 14pt; margin: 1.1em 0 0.35em; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 1.1em 0 0.3em; break-after: avoid; }
.num { display: inline-block; min-width: 1.9em; }
p { margin: 0 0 0.5em; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 0.6em; padding-left: 1.5em; }
li { margin-bottom: 0.12em; }
li.l1 { margin-left: 1.2em; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; overflow-wrap: anywhere; }
pre { font-family: "DejaVu Sans Mono", monospace; font-size: 8.2pt; line-height: 1.3; background: #f2f2f2;
  border-left: 2.5pt solid #888; padding: 5pt 7pt; margin: 0.5em 0 0.8em; white-space: pre-wrap;
  overflow-wrap: anywhere; text-align: left; hyphens: none; break-inside: avoid; }
pre code { font-size: inherit; }
pre.wide { font-size: 7.4pt; }
a { color: inherit; text-decoration: none; }
figure { margin: 0.6em 0; text-align: center; break-inside: avoid; }
figure img { width: 100%%; max-height: 20.5cm; object-fit: contain; }
figure.wide { page: wide; break-after: page; }
figure.wide img { max-height: 13.2cm; }
figcaption, p.caption { font: 9.5pt/1.3 "Liberation Sans", sans-serif; text-align: left; hyphens: none; }
figcaption { margin-top: 0.4em; }
p.caption { margin: 0 0 0.3em; break-after: avoid; }
.tablebox { margin: 0.7em 0 0.9em; break-inside: avoid; }
table { border-collapse: collapse; width: 100%%; font: 9pt/1.3 "Liberation Sans", sans-serif; text-align: left; hyphens: none; }
th, td { border: 0.5pt solid #777; padding: 2.5pt 4.5pt; vertical-align: top; overflow-wrap: break-word; }
th { background: #e2e2e2; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
td code, th code { font-size: 8.3pt; overflow-wrap: break-word; }   /* a file name keeps its width in a table column */
.title-page { break-after: page; text-align: center; padding-top: 5cm; }
.title-page h1 { font-size: 26pt; text-align: center; margin-bottom: 0.3em; }
.title-page .subtitle { font: 16pt "Liberation Sans", sans-serif; margin-bottom: 3cm; }
.title-page .field { font: 12pt/1.9 "Liberation Sans", sans-serif; }
.ph { background: #ffff00; }
.abstract { break-before: page; }
section.appendix { break-before: page; }
.abstract-title, .toc-title { font-size: 19pt; font-weight: bold; margin: 0 0 0.8em; }
.toc { break-before: page; }
.toc ul { list-style: none; padding: 0; margin: 0; text-align: left; }
.toc li { margin: 0 0 0.25em; }
.toc li.t1 { margin-top: 0.7em; font: bold 11pt "Liberation Sans", sans-serif; }
.toc li.t2 { margin-left: 1.2em; }
.toc li.t3 { margin-left: 2.4em; font-size: 10.5pt; }
.toc li.t3 .num { min-width: 2.5em; }
.toc a::after { content: leader('.') " " target-counter(attr(href), page); font-weight: normal; }
.refs { list-style: none; padding: 0; }
.refs li { margin-bottom: 0.45em; padding-left: 2.2em; text-indent: -2.2em; text-align: left; }
.refs .n { display: inline-block; width: 2.2em; text-indent: 0; }
"""


def title_page_html(shown):
    rows = [("Student", "student_name"), ("Register number", "register_no"), ("Course", "course"),
            ("Faculty", "faculty_name"), ("Institute", "institute"), ("Team number", "team_number"),
            ("Date", "date")]
    fields = []
    for label, key in rows:
        v = shown[key]
        val = '<span class="ph">%s</span>' % htmllib.escape(v) if v == PLACEHOLDERS[key] else htmllib.escape(v)
        fields.append('<div class="field"><strong>%s:</strong> %s</div>' % (label, val))
    return ('<section class="title-page"><h1>%s</h1><div class="subtitle">Project Report</div>%s</section>'
            % (htmllib.escape(shown["project_title"]), "".join(fields)))


def load_report_sources(rdir, repo=REPO):
    """Read the abstract, the chapters, the appendices and references.md from `rdir`: (chapters, references).
    `{{gen:NAME}}` lines are expanded from the repo's result files first."""
    rdir = Path(rdir)
    chapters = []
    files = ([(REPORT_ABSTRACT, "abstract")] + [(c, "chapter") for c in REPORT_CHAPTERS]
             + [(a, "appendix") for a in REPORT_APPENDICES])
    for name, kind in files:
        p = rdir / name
        if not p.exists():
            raise BuildError("report source is missing: %s" % p)
        text = expand_generated(p.read_text(encoding="utf-8"), repo)
        chapters.append({"file": name, "kind": kind, "text": text, "blocks": parse_report_file(name, text, rdir)})
    rp = rdir / REPORT_REFERENCES
    if not rp.exists():
        raise BuildError("report source is missing: %s" % rp)
    return chapters, parse_references(rp.read_text(encoding="utf-8"))


def check_report_text(chapters):
    """Problems in the source text: placeholders, a wrong abstract length, a wrong abstract structure."""
    problems = []
    for ch in chapters:
        for n, line in enumerate(ch["text"].splitlines(), 1):
            if RE_PLACEHOLDER.search(line):
                problems.append("%s line %d: leftover placeholder: %r" % (ch["file"], n, line.strip()[:80]))
    ab = [c for c in chapters if c["kind"] == "abstract"][0]
    words = count_words(" ".join(runs_text(b["runs"]) for b in ab["blocks"] if b["t"] == "p"))
    lo, hi = ABSTRACT_WORDS
    if not lo <= words <= hi:
        problems.append("abstract has %d words (must be %d to %d)" % (words, lo, hi))
    return problems


def check_page_count(pages, bounds=REPORT_PAGES):
    lo, hi = bounds
    if not lo <= pages <= hi:
        return ["page count %d is outside %d to %d" % (pages, lo, hi)]
    return []


def build_report_html(rdir, shown, repo=REPO):
    """Assemble the whole report as HTML. Returns a dict: html, problems (BuildError text), warnings, stats."""
    chapters, refs = load_report_sources(rdir, repo)
    problems = check_report_text(chapters)
    numbered = [c for c in chapters if c["kind"] != "abstract"]
    labels = number_report(chapters)
    res = Resolver(labels, refs)
    body, appendix_body = [], []
    for ch in chapters:
        if ch["kind"] == "appendix":
            appendix_body.append('<section class="appendix">%s</section>' % blocks_html(ch["blocks"], res, rdir))
        elif ch["kind"] == "abstract":
            inner = "".join("<p>%s</p>" % res.runs(b["runs"]) for b in ch["blocks"] if b["t"] == "p")
            body.append('<section class="abstract"><div class="abstract-title">Abstract</div>%s</section>' % inner)
        else:
            body.append("<section>%s</section>" % blocks_html(ch["blocks"], res, rdir))
    ref_items = "".join('<li id="ref-%s"><span class="n">[%d]</span>%s</li>'
                        % (k, i, res.runs(parse_inline(refs[k]))) for i, k in enumerate(res.cited, 1))
    body.append('<section><h1 class="chapter" id="sec-references">References</h1><ol class="refs">%s</ol></section>'
                % ref_items)
    body += appendix_body                  # order of the document: chapters, References, appendices
    toc_src = toc_entries(chapters, "chapter") + [(1, "", "References", "sec-references")]
    toc_src += toc_entries(chapters, "appendix")
    toc = "".join('<li class="t%d"><a href="#%s">%s%s</a></li>' % (
        lvl, anchor, ('<span class="num">%s</span> ' % num) if num else "", htmllib.escape(title))
        for lvl, num, title, anchor in toc_src)
    problems += res.errors
    unused_refs = [k for k in refs if k not in res.cited]
    unused_labels = [k for k in labels if not k.startswith("sec:") and k not in res.used]
    html = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>%s</title>'
            '<meta name="author" content="%s"><style>%s</style></head><body>%s'
            '<section class="toc"><div class="toc-title">Contents</div><ul>%s</ul></section>%s</body></html>'
            % (htmllib.escape(shown["project_title"]), htmllib.escape(shown["student_name"]),
               REPORT_CSS % {"header": css_string(shown["project_title"][:REPORT_SHORT_TITLE_MAX])},
               title_page_html(shown) + body[0], toc, "".join(body[1:])))
    stats = {"figures": [(l, k) for k, (l, _) in labels.items() if k.startswith("fig:")],
             "tables": [(l, k) for k, (l, _) in labels.items() if k.startswith("tab:")],
             "cited": list(res.cited), "chapters": len([c for c in numbered if c["kind"] == "chapter"]),
             "appendices": len([c for c in numbered if c["kind"] == "appendix"]),
             "toc": [(n, t) for _, n, t, _ in toc_src]}
    warnings = []
    if unused_refs:
        warnings.append("reference entries never cited: %s" % ", ".join(unused_refs))
    if unused_labels:
        warnings.append("figures/tables never referred to in the text: %s" % ", ".join(unused_labels))
    return {"html": html, "problems": problems, "warnings": warnings, "stats": stats, "chapters": chapters}


def pdf_pages_text(pdf_path):
    """Text of every page of a PDF (pypdfium2)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def render_pdf_pages(pdf_path, png_dir, scale=1.3):
    """Render every page of the PDF to PNG files page-NN.png in png_dir; returns their paths."""
    png_dir = assert_outside_repo(png_dir)
    import pypdfium2 as pdfium
    png_dir.mkdir(parents=True, exist_ok=True)
    for old in png_dir.glob("page-*.png"):
        old.unlink()
    doc = pdfium.PdfDocument(str(pdf_path))
    paths = []
    try:
        for i in range(len(doc)):
            target = assert_outside_repo(png_dir / ("page-%02d.png" % (i + 1)))
            doc[i].render(scale=scale).to_pil().save(str(target))
            paths.append(target)
    finally:
        doc.close()
    return paths


def verify_report_pdf(pages_text, chapters):
    """Problems found in the text of the finished PDF."""
    problems = check_page_count(len(pages_text))
    for n, t in enumerate(pages_text, 1):
        for m in RE_UNRESOLVED.finditer(t):
            problems.append("page %d: unresolved marker %r" % (n, t[m.start():m.start() + 20]))
        for m in RE_PLACEHOLDER.finditer(t):
            problems.append("page %d: placeholder %r" % (n, m.group(0)))
    if len(pages_text) > 1:
        if re.search(r"Page \d+ of \d+", pages_text[0]):
            problems.append("the title page has a page number")
        want = "Page 2 of %d" % len(pages_text)
        if want not in re.sub(r"\s+", " ", pages_text[1]):
            problems.append("footer %r not found on page 2" % want)
    flat = re.sub(r"\s+", " ", " ".join(pages_text))
    for ch in chapters:
        for b in ch["blocks"]:
            if b["t"] == "h" and re.sub(r"\s+", " ", b["text"]) not in flat:
                problems.append("heading missing from the PDF text: %r" % b["text"])
    return problems


def report_pdf_write(html, rdir, target):
    """Render `html` to `target` with WeasyPrint; returns the warnings WeasyPrint logged."""
    target = assert_outside_repo(target)
    from weasyprint import HTML
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Grab(level=logging.WARNING)
    log = logging.getLogger("weasyprint")
    log.addHandler(handler)
    try:
        HTML(string=html, base_url=str(Path(rdir).resolve()) + "/").write_pdf(str(target))
    finally:
        log.removeHandler(handler)
    return records


def cmd_report(args):
    out = assert_outside_repo(args.out)
    if not importlib.util.find_spec("weasyprint") or not importlib.util.find_spec("pypdfium2"):
        modules = probe_modules(out)
        vpy = venv_python(out)
        if modules.get("weasyprint") == "venv" and modules.get("pypdfium2") == "venv" and vpy.exists():
            os.execv(str(vpy), [str(vpy), str(Path(__file__).resolve())] + sys.argv[1:])
        raise BuildError("weasyprint and pypdfium2 are missing. Run:\n  %s/venv/bin/pip install %s"
                         % (out, " ".join(PIP_PDF)))
    for f in REPORT_FONTS:
        if font_available(f) is False:
            raise BuildError("font %r is not installed (check with: fc-list | grep -i '%s')" % (f, f))
    cfg = load_config(out)
    shown, empty = config_values(cfg)
    if empty:
        print("WARNING: empty config values shown as placeholders on the title page: %s" % ", ".join(empty))
        print("         edit %s and rebuild" % (out / "config.json"))
    rdir = out / "report"
    built = build_report_html(rdir, shown)
    build_dir = assert_outside_repo(out / "build")
    build_dir.mkdir(parents=True, exist_ok=True)
    assert_outside_repo(build_dir / "report.html").write_text(built["html"], encoding="utf-8")
    if built["problems"]:
        raise BuildError("report sources FAILED (%d problems):\n  - %s" % (len(built["problems"]),
                                                                       "\n  - ".join(built["problems"][:40])))
    target = assert_outside_repo(out / REPORT_PDF)
    logged = report_pdf_write(built["html"], rdir, target)
    bad = [m for m in logged if "ailed to load" in m or "issing" in m]
    pages_text = pdf_pages_text(target)
    problems = verify_report_pdf(pages_text, built["chapters"]) + bad
    if problems:
        raise BuildError("PDF verification FAILED (%d problems):\n  - %s" % (len(problems), "\n  - ".join(problems[:40])))
    pngs = render_pdf_pages(target, build_dir / "report_pages")
    st = built["stats"]
    print("built %s (%d KB, %d pages)" % (target, target.stat().st_size // 1024, len(pages_text)))
    print("  chapters %d; appendices %d; figures %d; tables %d; references cited %d"
          % (st["chapters"], st["appendices"], len(st["figures"]), len(st["tables"]), len(st["cited"])))
    for ch in built["chapters"]:
        words = count_words(" ".join(t for t in (runs_text(b["runs"]) for b in ch["blocks"] if b["t"] in ("p", "li"))))
        print("    %-20s %5d words" % (ch["file"], words))
    print("  figures: " + ", ".join("%s (%s)" % (l, k[4:]) for l, k in st["figures"]))
    print("  tables: " + ", ".join("%s (%s)" % (l, k[4:]) for l, k in st["tables"]))
    print("  references cited: " + ", ".join(st["cited"]))
    print("  verification: OK (no placeholders or {{gen:}} markers, all cross-references and citations resolved, figures present,")
    print("                abstract length, page count %d to %d, footer and fonts)" % REPORT_PAGES)
    for m in sorted(set(logged)):
        if "fontTools instead of HarfBuzz" not in m:     # harmless notice about font subsetting
            print("  weasyprint: %s" % m)
    for w in built["warnings"]:
        print("  WARNING: %s" % w)
    print("  page images: %s (%d files)" % (build_dir / "report_pages", len(pngs)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output folder (default ~/minibox-deliverables; never inside the repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-tools")
    sub.add_parser("coverage")
    sub.add_parser("docs")
    sub.add_parser("report")
    args = ap.parse_args(argv)
    try:
        assert_outside_repo(args.out)
        return {"check-tools": cmd_check_tools, "coverage": cmd_coverage, "docs": cmd_docs,
                "report": cmd_report}[args.cmd](args)
    except RepoWriteError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except BuildError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
