#!/usr/bin/env python3
"""make_charts.py - turn a bench results directory into SVG charts (standard library only).

Usage: python3 bench/make_charts.py [RESULTS_DIR] [--out DIR]
    RESULTS_DIR defaults to bench/results/latest; charts go to RESULTS_DIR unless --out is given.
Writes startup.svg, memory.svg and limits.svg. Works offline, needs no root.
Colours are CSS variables with a dark-mode override, so the files read well on both themes.
All text from the CSV files goes through escape() before it enters the SVG.
"""
import argparse
import csv
import math
import os
import sys
from xml.sax.saxutils import escape

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_STARTUP_SLOT = 150          # px per startup bar, so 7 configurations do not overlap
CHAR_W = 7.0                    # estimated width of one 12 px character

STYLE = """
<style>
  svg { --bg:#ffffff; --fg:#1f2328; --muted:#656d76; --grid:#d8dee4;
        --c1:#2f6feb; --c2:#d4761a; --c3:#2c9a5b; --pass:#1a7f37; --fail:#cf222e; }
  @media (prefers-color-scheme: dark) {
    svg { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --grid:#30363d;
          --c1:#6ea0ff; --c2:#f0a050; --c3:#4cc38a; --pass:#3fb950; --fail:#ff7b72; }
  }
  .bg { fill:var(--bg); } text { fill:var(--fg); font-family:system-ui,Segoe UI,Helvetica,Arial,sans-serif; }
  .title { font-size:18px; font-weight:600; } .sub { font-size:12px; fill:var(--muted); }
  .axis, .tick { font-size:12px; fill:var(--muted); } .lbl { font-size:12px; }
  .grid { stroke:var(--grid); stroke-width:1; } .whisk { stroke:var(--fg); stroke-width:2; fill:none; }
  .c1 { fill:var(--c1); } .c2 { fill:var(--c2); } .c3 { fill:var(--c3); }
  .pass { fill:var(--pass); font-weight:600; } .fail { fill:var(--fail); font-weight:600; }
  .note { font-size:12px; font-style:italic; fill:var(--muted); }
  .dash { stroke:var(--fg); stroke-width:1.5; stroke-dasharray:5 4; }
</style>
"""


def read_csv(path):
    """List of dict rows, or [] when the file is missing."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def t(x, y, text, cls="lbl", anchor="start", extra=""):
    return '<text x="%.1f" y="%.1f" class="%s" text-anchor="%s"%s>%s</text>' % (
        x, y, cls, anchor, extra, escape(str(text)))


def wrap(width, height, title, subtitle, body):
    """The <svg> shell: title, subtitle, theme style, background."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
            'role="img" aria-label="%s">\n<title>%s</title>%s\n<rect class="bg" width="100%%" height="100%%"/>\n'
            '%s\n%s\n%s\n</svg>\n') % (
        width, height, width, height, escape(title, {'"': "&quot;"}), escape(title), STYLE,
        t(24, 30, title, "title"), t(24, 50, subtitle, "sub"), body)


def nice_max(v):
    """Round v up to a 1/2/5 x 10^k value so the axis ends on a clean number."""
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def linear_ticks(vmax_data):
    """Round-number ticks from 0 to just above the data: at most 6 steps of a 1/2/5 x 10^k size."""
    v = max(vmax_data, 1e-9) * 1.05
    mag = 10 ** math.floor(math.log10(v))
    for m in (0.1, 0.2, 0.5, 1, 2, 5, 10):
        step = m * mag
        if math.ceil(v / step - 1e-9) <= 6:
            n = max(1, math.ceil(v / step - 1e-9))
            return [round(step * i, 10) for i in range(n + 1)]
    return [0, v]


def log_ticks(lo, hi):
    """1-2-5 ticks for a log axis: the largest tick <= lo up to the smallest tick >= hi."""
    allt = [m * 10 ** e for e in range(-3, 7) for m in (1, 2, 5)]
    low = max([x for x in allt if x <= lo * (1 + 1e-9)] or [allt[0]])
    high = min([x for x in allt if x >= hi * (1 - 1e-9)] or [allt[-1]])
    if high <= low:
        high = min([x for x in allt if x > low])
    return [x for x in allt if low <= x <= high]


def fmt_tick(v):
    return ("%g" % v)


def y_axis(left, top, w, h, ticks, unit, log=False):
    """Gridlines and labels at `ticks`; returns (svg lines, pos) where pos(v) is the y of value v."""
    lo, hi = ticks[0], ticks[-1]
    if log:
        def pos(v):
            v = min(max(v, lo), hi)
            return top + h - h * (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))
    else:
        def pos(v):
            return top + h - h * (v - lo) / (hi - lo)
    out = []
    for v in ticks:
        y = pos(v)
        out.append('<line class="grid" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (left, left + w, y, y))
        out.append(t(left - 8, y + 4, fmt_tick(v), "tick", "end"))
    out.append('<text class="axis" transform="translate(16,%.1f) rotate(-90)" text-anchor="middle">%s</text>'
               % (top + h / 2, escape(unit)))
    return out, pos


def bar_label(med, p95):
    """The single label above a startup bar."""
    return "%.1f ms (p95 %.1f)" % (med, p95)


def wrap_label(name, max_chars=16):
    """Split a configuration name at its underscores into lines of at most max_chars characters."""
    lines = []
    for word in name.split("_"):
        if lines and len(lines[-1]) + 1 + len(word) <= max_chars:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def n_summary(measured):
    """'n=30 per configuration' when every n is equal, else 'name n=..' for each (long, but rare)."""
    ns = {r["n"] for r in measured}
    if len(ns) == 1:
        return "n=%s per configuration" % ns.pop()
    return ", ".join("%s n=%s" % (r["config"], r["n"]) for r in measured)


def not_measured_note(config, note):
    """One footnote line: 'Not measured: docker (docker CLI not found)'.

    The CSV note already starts with 'not measured:' and sometimes with the
    configuration name, so both prefixes are removed before the line is built."""
    reason = (note or "").strip()
    for prefix in ("not measured:", config + ":"):
        if reason.lower().startswith(prefix.lower()):
            reason = reason[len(prefix):].strip()
    if reason.lower().startswith("not measured"):
        reason = reason[len("not measured"):].strip(" :")
    if not reason:
        return "Not measured: %s" % config
    return "Not measured: %s (%s)" % (config, reason)


def startup_svg(rows):
    """Bar = median startup time on a log axis, whisker up to p95. Not-measured configs get a note."""
    measured = [r for r in rows if r.get("status") == "measured" and num(r.get("median_ms")) is not None]
    missing = [r for r in rows if r not in measured]
    p95s = [num(r.get("p95_ms")) or num(r["median_ms"]) for r in measured] or [10.0]
    meds = [num(r["median_ms"]) for r in measured] or [1.0]
    ticks = log_ticks(min(meds), max(p95s))
    # at least 150 px per bar: "164.4 ms (p95 194.3)" is about 140 px wide, so neighbours never touch
    left, top, h = 80, 90, 300
    w = max(840, MIN_STARTUP_SLOT * len(rows))
    width = left + w + 40
    slot = w / max(len(rows), 1)
    axis, pos = y_axis(left, top, w, h, ticks, "Wall-clock time to run /bin/true (ms, log scale)", log=True)
    body = axis
    base = top + h
    for i, r in enumerate(rows):
        cx = left + slot * (i + 0.5)
        if r in measured:
            med, p95 = num(r["median_ms"]), num(r.get("p95_ms")) or num(r["median_ms"])
            bw = slot * 0.45
            ym, yp = pos(med), pos(p95)
            body.append('<rect class="c1" x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>' %
                        (cx - bw / 2, ym, bw, base - ym))
            body.append('<path class="whisk" d="M%.1f %.1f V%.1f M%.1f %.1f H%.1f M%.1f %.1f H%.1f"/>' % (
                cx, ym, yp, cx - 8, yp, cx + 8, cx - 8, ym, cx + 8))
            body.append(t(cx, yp - 8, bar_label(med, p95), "tick", "middle"))
        else:
            body.append(t(cx, base - 8, "not measured", "note", "middle"))
        for k, line in enumerate(wrap_label(r["config"])):
            body.append(t(cx, base + 22 + 16 * k, line, "lbl", "middle"))
    notes = [not_measured_note(r["config"], r.get("note")) for r in missing]
    for j, n in enumerate(notes):
        body.append(t(24, base + 58 + 16 * j, n, "note"))
    n_txt = n_summary(measured)
    height = base + 72 + 16 * len(notes)
    return wrap(width, height, "Startup time per configuration",
                "Log scale. Bar = median, whisker = p95 (lower is better). " + n_txt, "\n".join(body))


def legend_layout(items, left, width, y0, row_h=22, swatch=12, gap=24, char_w=7.0):
    """Place legend entries left to right, wrapping to a new row when the width is used up.
    items: list of label strings. Returns (x, y, text_x, text_end) per item; entries never overlap."""
    out, x, y = [], left, y0
    for lab in items:
        tw = len(lab) * char_w
        end = x + swatch + 6 + tw
        if x > left and end > left + width:
            x, y = left, y + row_h
            end = x + swatch + 6 + tw
        out.append((x, y, x + swatch + 6, end))
        x = end + gap
    return out


def memory_svg(rows, incr):
    """Grouped bars: per-container memory for k = 1, 5, 10 containers of sleep 300."""
    ks = sorted({int(r["k"]) for r in rows if (r.get("k") or "").isdigit()})
    series = [("cgroup memory.current", "cgroup_memory_current_total", "c1", "minibox"),
              ("init PSS", "init_mem_total", "c2", "minibox"),
              ("monitor PSS", "monitor_or_shim_mem_total", "c3", "minibox")]

    def per(cfg, k, col):
        for r in rows:
            if r["config"] == cfg and r.get("k") == str(k):
                v = num(r.get(col))
                return None if v is None else v / k / 1048576.0
        return None

    vals = [per("minibox", k, c) for k in ks for _, c, _, _ in series]
    host = {k: per("host_sleep", k, "init_mem_total") for k in ks}
    dock = {k: per("docker", k, "cgroup_memory_current_total") for k in ks}
    allv = [v for v in vals + list(host.values()) + list(dock.values()) if v is not None]
    ticks = linear_ticks(max(allv) if allv else 1)
    left, top, w, h = 80, 80, 760, 300
    axis, pos = y_axis(left, top, w, h, ticks, "Memory per container (MiB)")
    body = axis
    base = top + h
    slot = w / max(len(ks), 1)
    for gi, k in enumerate(ks):
        gx = left + slot * gi + slot * 0.1
        bw = slot * 0.8 / len(series)
        for si, (lab, col, cls, cfg) in enumerate(series):
            v = per(cfg, k, col)
            if v is None:
                continue
            y = pos(v)
            x = gx + si * bw
            body.append('<rect class="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>' %
                        (cls, x + 2, y, bw - 4, base - y))
            body.append(t(x + bw / 2, y - 4, "%.2f" % v, "tick", "middle"))
        for ref, extra in ((host.get(k), ""), (dock.get(k), ' stroke-dasharray="2 3"')):
            if ref is not None:
                y = pos(ref)
                body.append('<line class="dash" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"%s/>' %
                            (gx, gx + bw * len(series), y, y, extra))
        body.append(t(left + slot * (gi + 0.5), base + 20, "%d container%s" % (k, "" if k == 1 else "s"), "lbl", "middle"))
    labels = ["minibox " + lab for lab, _, _, _ in series] + ["host sleep PSS (dashed line)"]
    lay = legend_layout(labels, left, w, base + 48)
    for i, (x, y, tx, _end) in enumerate(lay):
        if i < len(series):
            body.append('<rect class="%s" x="%.1f" y="%.1f" width="12" height="12"/>' % (series[i][2], x, y - 11))
        else:
            body.append('<line class="dash" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (x, x + 12, y - 5, y - 5))
        body.append(t(tx, y, labels[i]))
    ny = lay[-1][1] + 28
    if not any(v is not None for v in dock.values()):
        note = next((r.get("note") for r in rows if r["config"] == "docker" and r.get("note")), "")
        body.append(t(24, ny, not_measured_note("docker", note), "note"))
        ny += 16
    for r in incr:
        v = num(r.get("incremental_bytes_per_container"))
        if v is not None:
            body.append(t(24, ny, "Incremental cost per extra container (%s): %.2f MiB" % (r["config"], v / 1048576.0), "note"))
            ny += 16
    return wrap(900, ny + 10, "Memory per container running sleep 300",
                "Total divided by number of containers (lower is better); PSS = proportional set size",
                "\n".join(body))


def limits_svg(rows):
    """Simple table: test, expected, measured, status OK/FAIL/ERROR/TIMEOUT (text and mark, not colour only)."""
    cols = [("Test", 24, 200), ("Expected", 230, 280), ("Measured", 520, 250), ("Result", 780, 80)]
    rh = 28
    top = 80
    body = []
    for name, x, _ in cols:
        body.append(t(x, top, name, "axis", extra=' style="font-weight:600"'))
    body.append('<line class="grid" x1="16" x2="884" y1="%d" y2="%d"/>' % (top + 8, top + 8))
    for i, r in enumerate(rows):
        y = top + 8 + rh * (i + 1)
        body.append(t(24, y, r["test"]))
        body.append(t(230, y, r["expected"][:44]))
        body.append(t(520, y, r["measured"][:40]))
        status = r.get("status") or r.get("result") or "?"   # older CSVs called it "result"
        ok = status in ("OK", "PASS")
        mark = "✓ " if ok else ("⚠ " if status in ("ERROR", "TIMEOUT") else "✗ ")
        body.append(t(780, y, mark + status, "pass" if ok else "fail"))
        body.append('<line class="grid" x1="16" x2="884" y1="%d" y2="%d"/>' % (y + 8, y + 8))
    statuses = [r.get("status") or r.get("result") or "?" for r in rows]
    passed = sum(1 for x in statuses if x in ("OK", "PASS"))
    broken = sum(1 for x in statuses if x in ("ERROR", "TIMEOUT"))
    sub = "%d of %d checks OK (expected vs measured, read from cgroup files)" % (passed, len(rows))
    if broken:
        sub += "; %d ERROR/TIMEOUT: the check did not complete" % broken
    return wrap(900, top + 8 + rh * (len(rows) + 1) + 20, "Limit enforcement", sub,
                "\n".join(body))


def make_all(results_dir, out_dir):
    """Write the three SVGs; return the list of paths written. Missing CSVs are skipped."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    jobs = [("startup.svg", "startup_summary.csv", lambda r: startup_svg(r)),
            ("memory.svg", "memory.csv", lambda r: memory_svg(r, read_csv(os.path.join(results_dir, "memory_incremental.csv")))),
            ("limits.svg", "limits.csv", lambda r: limits_svg(r))]
    for svg, csv_name, fn in jobs:
        rows = read_csv(os.path.join(results_dir, csv_name))
        if not rows:
            print("skipped %s: %s missing or empty" % (svg, csv_name))
            continue
        path = os.path.join(out_dir, svg)
        with open(path, "w", encoding="utf-8") as f:
            f.write(fn(rows))
        written.append(path)
        print("wrote", path)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="make SVG charts from a bench results directory")
    ap.add_argument("results_dir", nargs="?", default=os.path.join(ROOT_DIR, "bench", "results", "latest"))
    ap.add_argument("--out", help="output directory (default: the results directory)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.results_dir):
        print("results directory not found: %s (run bench.py first)" % args.results_dir)
        return 1
    return 0 if make_all(args.results_dir, args.out or args.results_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
