#!/usr/bin/env python3
"""Write the SVG diagrams of the project report (standard library only) and copy its three benchmark charts.

  architecture.svg       components and how they talk to each other
  startup_sequence.svg   the container start-up sequence (parent, kernel, container process)
  cgroup_hierarchy.svg   the cgroup tree under /sys/fs/cgroup/minibox
  lifecycle.svg          container states and the `stop` escalation
  ui_request_path.svg    the checks a web-interface request passes before `minibox` is called
  startup.svg, memory.svg, limits.svg   the charts of bench/results/reference/ (made by bench/make_charts.py),
                         copied with the CSS variables resolved to the light theme, because WeasyPrint does not
                         evaluate them; nothing else is changed

The diagrams are grayscale (printable in black and white), use arrowheads and text of at
least 12 user units (about 9 pt at the size the report prints them), and every text is
checked to fit its box. Output goes to ~/minibox-deliverables/report/figures/ (override
with --out); a directory inside the repository is refused.
Details: docs/scripts/make_report_diagrams.md
"""
import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_deliverables import DEFAULT_OUT, RepoWriteError, assert_outside_repo, flatten_svg  # noqa: E402

WIDTH = 620                     # all diagrams are 620 units wide: printed at 16.4 cm, 12 units = 9 pt
FONT = "Liberation Sans, DejaVu Sans, Arial, sans-serif"
MONO = "DejaVu Sans Mono, Liberation Mono, monospace"
SIZE = 12
HEAD_LEN, HEAD_W = 10, 4       # arrowhead length and half width
LINE = 15                       # line height for 12-unit text
SANS_W = 0.58                   # average glyph width / font size (a safe upper bound)
MONO_W = 0.61

INK, DARK, MID, LIGHT, PALE, WHITE = "#000000", "#333333", "#666666", "#dddddd", "#f2f2f2", "#ffffff"
FIGURES = ("architecture", "startup_sequence", "cgroup_hierarchy", "lifecycle", "ui_request_path")
CHARTS = ("startup", "memory", "limits")          # bench/results/reference/<name>.svg, copied in the light theme
CHART_SOURCE = Path(__file__).resolve().parent.parent / "bench" / "results" / "reference"


def text_width(s, size=SIZE, mono=False):
    """Upper-bound width of one line of text, in user units."""
    return len(s) * size * (MONO_W if mono else SANS_W)


class Svg:
    """A tiny SVG builder; every text is checked against the width it has to fit."""

    def __init__(self, height, title, desc):
        self.h = height
        self.title, self.desc = title, desc
        self.body = []

    # --- primitives
    def add(self, s):
        self.body.append(s)

    def text(self, x, y, s, size=SIZE, anchor="middle", bold=False, mono=False, fill=INK, fit=None):
        limit = fit if fit is not None else WIDTH - 4
        if text_width(s, size, mono) > limit:
            raise ValueError("text does not fit (%d > %d): %r" % (text_width(s, size, mono), limit, s))
        self.add('<text x="%g" y="%g" font-size="%g" text-anchor="%s" font-family="%s" font-weight="%s" '
                 'fill="%s">%s</text>' % (x, y, size, anchor, MONO if mono else FONT,
                                           "bold" if bold else "normal", fill, escape(s)))

    def lines(self, x, y, lines, anchor="middle", bold_first=False, fit=None, mono=False):
        for i, s in enumerate(lines):
            self.text(x, y + i * LINE, s, anchor=anchor, bold=bold_first and i == 0, fit=fit, mono=mono)

    def box(self, x, y, w, h, lines, fill=WHITE, dash=False, bold_first=True, rx=3, mono=False):
        self.add('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" stroke="%s" stroke-width="1.3"%s/>'
                 % (x, y, w, h, rx, fill, INK, ' stroke-dasharray="6 4"' if dash else ""))
        top = y + (h - len(lines) * LINE) / 2 + SIZE - 2
        self.lines(x + w / 2, top, lines, bold_first=bold_first, fit=w - 8, mono=mono)

    def line(self, pts, dash=False, arrow=False, width=1.4):
        """A polyline; with arrow=True a filled arrowhead (a polygon, drawn explicitly so that every
        renderer shows it pointing the right way) ends it at the last point."""
        pts = list(pts)
        head = ""
        if arrow:
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
            dx, dy = x1 - x0, y1 - y0
            n = (dx * dx + dy * dy) ** 0.5
            ux, uy = dx / n, dy / n
            bx, by = x1 - ux * HEAD_LEN, y1 - uy * HEAD_LEN          # base of the head
            head = ('<polygon class="arrowhead" points="%g,%g %g,%g %g,%g" fill="%s" stroke="%s" '
                    'stroke-width="0.5"/>' % (x1, y1, bx - uy * HEAD_W, by + ux * HEAD_W,
                                              bx + uy * HEAD_W, by - ux * HEAD_W, INK, INK))
            pts[-1] = (bx, by)
        p = " ".join("%g,%g" % pt for pt in pts)
        self.add('<polyline points="%s" fill="none" stroke="%s" stroke-width="%g"%s/>'
                 % (p, INK, width, ' stroke-dasharray="6 4"' if dash else ""))
        if head:
            self.add(head)

    def arrow(self, pts, dash=False):
        self.line(pts, dash=dash, arrow=True)

    def label_bg(self, cx, y_first, lines, size=SIZE):
        """White patch behind a label so lifelines and lines do not run through the letters."""
        w = max(text_width(s, size) for s in lines) + 8
        self.add('<rect x="%g" y="%g" width="%g" height="%g" fill="%s"/>'
                 % (cx - w / 2, y_first - SIZE, w, len(lines) * LINE + 1, WHITE))

    def render(self):
        head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%g" viewBox="0 0 %d %g" '
                'role="img" aria-labelledby="t d">\n<title id="t">%s</title>\n<desc id="d">%s</desc>\n'
                '<rect x="0" y="0" width="%d" height="%g" fill="%s"/>\n'
                % (WIDTH, self.h, WIDTH, self.h, escape(self.title), escape(self.desc), WIDTH, self.h, WHITE))
        return head + "\n".join(self.body) + "\n</svg>\n"


# ------------------------------------------------------------------ (a) architecture

def architecture():
    s = Svg(492, "Component architecture of MiniBox",
            "The web UI calls the minibox CLI; the CLI starts a monitor process per container; the monitor "
            "creates the container, its cgroup and its veth pair and records state in the state directory.")
    cx, cw = 446, 166                       # right-hand column: x and width
    mid = cx + cw / 2
    s.box(8, 20, 180, 52, ["Web UI (Flask)", "ui/app.py"])
    s.box(8, 130, 180, 62, ["minibox CLI", "run, ps, logs, stop, rm,", "exec"])
    s.box(240, 130, 150, 62, ["Monitor process", "waits, cleans up,", "records the exit"])
    s.box(cx, 130, cw, 62, ["Container init", "PID 1 of the new", "PID namespace"])
    s.box(cx, 208, cw, 58, ["with --init:", "minibox-init is PID 1,", "the command is PID 2"], dash=True,
          bold_first=False)
    s.box(8, 290, 180, 74, ["State directory", "/var/lib/minibox/", "containers/<id>/", "files: state, log"])
    s.box(cx, 284, cw, 58, ["New namespaces", "PID, UTS, mount,", "IPC, network"])
    s.box(cx, 354, cw, 46, ["cgroup", "minibox/box-<id>"])
    s.box(cx, 412, cw, 62, ["veth pair", "eth0 (container) -", "vethh<pid> (host)"])
    s.arrow([(98, 72), (98, 130)])
    s.text(106, 108, "argv list", anchor="start", fit=80)
    s.arrow([(188, 161), (240, 161)])
    s.text(214, 153, "fork", fit=48)
    s.arrow([(390, 161), (cx, 161)])
    s.text(418, 153, "clone3", fit=52)
    s.line([(mid, 192), (mid, 208)])
    s.arrow([(98, 192), (98, 290)])
    s.lines(106, 236, ["read by ps,", "logs, stop, rm"], anchor="start", fit=110)
    s.arrow([(330, 192), (330, 327), (188, 327)])
    s.text(324, 262, "writes state, log", anchor="end", fit=130)
    s.arrow([(358, 192), (358, 377), (cx, 377)])
    s.lines(352, 346, ["mkdir + limits,", "rmdir after exit"], anchor="end", fit=130)
    s.arrow([(382, 192), (382, 443), (cx, 443)])
    s.lines(376, 415, ["ip link (only", "with --net veth)"], anchor="end", fit=130)
    s.arrow([(175, 130), (175, 92), (mid, 92), (mid, 130)], dash=True)
    s.text(348, 86, "exec: setns into the container", fit=212)
    s.lines(8, 410, ["Dashed: optional, or via exec.", "Foreground run: the CLI", "process is itself the monitor."],
            anchor="start", fit=215)
    return s.render()


# ------------------------------------------------------------------ (b) start-up sequence

SEQ_X = {"P": 100, "K": 310, "C": 520}


def startup_sequence():
    s = Svg(100, "Container start-up sequence",
            "Sequence diagram between the parent (minibox run or its monitor), the kernel and the container "
            "process: cgroup creation, clone3 into the cgroup, child set-up, ready/go handshake, veth set-up, "
            "exec, wait and clean-up.")
    items, y = [], 76

    def band(title):
        nonlocal y
        items.append(("band", y, title))
        y += 25

    def note(who, lines, dash=False):
        nonlocal y
        h = len(lines) * LINE + 8
        items.append(("note", y, who, lines, h, dash))
        y += h + 7

    def msg(a, b, lines, dash=False):
        nonlocal y
        yy = y + len(lines) * LINE + 2
        items.append(("msg", yy, a, b, lines, dash))
        y = yy + 11

    band("1  Preparation in the parent")
    note("P", ["check cgroup v2 (limits only)", "pipe2: ready and go pipes", "install SIGINT/SIGTERM handlers"])
    msg("P", "K", ["mkdir minibox/box-<id>,", "write the limits (limits only)"])
    band("2  Creation of the container process")
    msg("P", "K", ["clone3(CLONE_NEWPID|UTS|NS|IPC|NET", "| CLONE_INTO_CGROUP)"])
    msg("K", "C", ["new process, PID 1 of the new", "namespaces, already in the cgroup"])
    band("3  Set-up inside the container")
    note("C", ["sethostname, loopback up", "private mounts, bind rootfs,", "/dev, pivot_root, /proc, /tmp"])
    msg("C", "P", ["'R' on the ready pipe"], dash=True)
    note("C", ["block reading the go pipe"])
    note("P", ["--net veth: ip link add,", "move peer into the container's", "netns as eth0, set 10.0.n.1/24"])
    band("4  Release and exec")
    msg("P", "C", ["'G' on the go pipe", "('A' and SIGKILL if setup failed)"], dash=True)
    note("P", ["on_started: save PID", "in the state file"])
    note("C", ["--net veth: eth0 = 10.0.n.2/24", "chdir /, set PATH and HOME"])
    note("C", ["execvp(command); with --init", "minit_run: command is PID 2"])
    band("5  Run and clean-up")
    msg("P", "K", ["waitpid(container)"])
    msg("K", "P", ["exit status (128+N if signalled)"], dash=True)
    note("P", ["delete veth if still present,", "--stats and OOM check,", "rmdir box-<id>, record exit"])
    items.append(("fallback", y, ["Fallback without clone3 (ENOSYS, EPERM, ...) or with MINIBOX_NO_CLONE3=1:",
                                  "clone() creates the process; after 'R' the parent writes its PID to cgroup.procs."]))
    y += 2 * LINE + 22
    s.h = y

    heads = {"P": ["Parent", "minibox run / monitor"], "K": ["Kernel"], "C": ["Container process"]}
    for k, lines in heads.items():
        s.box(SEQ_X[k] - 90, 10, 180, 44, lines)
        s.add('<line x1="%d" y1="54" x2="%d" y2="%g" stroke="%s" stroke-width="1" stroke-dasharray="3 4"/>'
              % (SEQ_X[k], SEQ_X[k], y - 34, MID))
    for it in items:
        if it[0] == "band":
            _, yy, title = it
            s.add('<rect x="0" y="%g" width="%d" height="20" fill="%s"/>' % (yy, WIDTH, LIGHT))
            s.text(8, yy + 14, title, anchor="start", bold=True)
        elif it[0] == "note":
            _, yy, who, lines, h, dash = it
            x = min(max(SEQ_X[who] - 115, 4), WIDTH - 234)
            s.box(x, yy, 230, h, lines, fill=PALE, dash=dash, bold_first=False)
        elif it[0] == "msg":
            _, yy, a, b, lines, dash = it
            xa, xb = SEQ_X[a], SEQ_X[b]
            mid = (xa + xb) / 2
            s.label_bg(mid, yy - 4 - (len(lines) - 1) * LINE, lines)
            s.lines(mid, yy - 5 - (len(lines) - 1) * LINE, lines)
            s.arrow([(xa, yy), (xb, yy)], dash=dash)
        else:
            _, yy, lines = it
            s.add('<rect x="8" y="%g" width="%d" height="%g" rx="3" fill="%s" stroke="%s" stroke-width="1.3" '
                  'stroke-dasharray="6 4"/>' % (yy, WIDTH - 16, len(lines) * LINE + 12, WHITE, INK))
            s.lines(16, yy + 19, lines, anchor="start", fit=WIDTH - 30)
    return s.render()


# ------------------------------------------------------------------ (c) cgroup hierarchy

def cgroup_hierarchy():
    s = Svg(470, "cgroup hierarchy of MiniBox",
            "The root cgroup /sys/fs/cgroup contains the empty cgroup minibox, which contains one leaf cgroup "
            "box-<id> per container with limit files, statistic files and cgroup.procs.")
    s.box(8, 14, 300, 56, ["/sys/fs/cgroup  (root cgroup)", "cgroup.subtree_control:", "+memory +cpu +pids"],
          mono=False)
    s.lines(326, 36, ["Controllers memory, cpu and pids", "are enabled for the children."],
            anchor="start", fit=290)
    s.box(40, 112, 268, 62, ["minibox/", "created once, kept between runs", "holds no process"])
    s.lines(326, 126, ["No-internal-process rule: a", "cgroup that hands controllers to", "its children holds no process."],
            anchor="start", fit=290)
    s.arrow([(60, 70), (60, 112)])
    s.add('<rect x="72" y="212" width="540" height="196" rx="3" fill="%s" stroke="%s" stroke-width="1.3"/>'
          % (WHITE, INK))
    s.text(82, 232, "box-<container id>/", anchor="start", bold=True, fit=200)
    s.text(602, 232, "one per container, created when a limit is set", anchor="end", fit=330)
    s.add('<line x1="72" y1="242" x2="612" y2="242" stroke="%s" stroke-width="1"/>' % INK)
    s.text(82, 262, "Limits, written before clone3", anchor="start", bold=True, fit=250)
    rows = [("memory.max", "hard limit, bytes"), ("memory.swap.max", "0: no swap"),
            ("memory.oom.group", "1: kill whole group"), ("cpu.max", "quota per 100 ms"),
            ("pids.max", "max tasks")]
    for i, (name, desc) in enumerate(rows):
        yy = 284 + i * 22
        s.text(82, yy, name, anchor="start", mono=True, fit=135)
        s.text(222, yy, desc, anchor="start", fit=138)
    s.text(362, 262, "Read after the container ends", anchor="start", bold=True, fit=240)
    stats = [("memory.peak", "highest memory use"), ("cpu.stat", "CPU time used"),
             ("pids.peak", "highest task count"), ("memory.events", "oom_kill counter")]
    for i, (name, desc) in enumerate(stats):
        yy = 284 + i * 22
        s.text(362, yy, name, anchor="start", mono=True, fit=105)
        s.text(472, yy, desc, anchor="start", fit=138)
    s.text(362, 372, "cgroup.procs", anchor="start", mono=True, fit=105)
    s.text(472, 372, "live processes", anchor="start", fit=138)
    s.text(82, 398, "Members: container init; exec adds its commands.", anchor="start", fit=400)
    s.box(72, 424, 240, 34, ["box-<another container id>/"], dash=True, bold_first=False)
    s.line([(56, 174), (56, 441)])
    s.arrow([(56, 310), (72, 310)])
    s.arrow([(56, 441), (72, 441)])
    return s.render()


# ------------------------------------------------------------------ (d) life cycle and stop escalation

def lifecycle():
    s = Svg(576, "Container life cycle and stop escalation",
            "Top: the states registered, running, exited, removed and the derived state dead. Bottom: minibox "
            "stop sends SIGTERM, polls the state file for the grace period, then sends SIGKILL.")
    s.text(8, 16, "Container state (field status in <id>/state)", anchor="start", bold=True, fit=400)
    s.box(8, 84, 110, 52, ["registered", "status=running", "pid=0"])
    s.box(170, 84, 110, 52, ["running", "PID saved by", "on_started"])
    s.box(332, 84, 110, 52, ["exited", "exit code and", "time saved"])
    s.box(510, 84, 102, 52, ["removed", "directory", "deleted"])
    s.box(170, 168, 110, 52, ["dead", "shown by ps,", "never stored"], dash=True)
    s.arrow([(118, 110), (170, 110)])
    s.text(144, 103, "started", fit=52)
    s.arrow([(280, 110), (332, 110)])
    s.text(306, 103, "ends", fit=48)
    s.arrow([(442, 110), (510, 110)])
    s.text(476, 103, "rm", fit=60)
    s.arrow([(225, 136), (225, 168)])
    s.text(233, 156, "monitor killed", anchor="start", fit=110)
    s.arrow([(280, 194), (561, 194), (561, 136)], dash=True)
    s.text(420, 187, "rm kills the init, removes cgroup, veth", fit=300)
    s.arrow([(225, 84), (225, 58), (561, 58), (561, 84)])
    s.text(393, 52, "foreground run: removed when it ends", fit=260)
    s.text(8, 262, "minibox stop <id> [--time N]", anchor="start", bold=True, fit=300)
    s.box(8, 280, 150, 52, ["send SIGTERM", "to the container's", "PID"])
    s.box(200, 280, 186, 52, ["poll the state file", "every 100 ms, up to", "N s (default 5 s)"], bold_first=False)
    s.box(428, 280, 184, 52, ["status = exited?"], rx=20, bold_first=False)
    s.arrow([(158, 306), (200, 306)])
    s.arrow([(386, 306), (428, 306)])
    s.arrow([(580, 332), (580, 380)])
    s.text(588, 360, "yes", anchor="start", fit=30)
    s.box(500, 380, 112, 46, ["stopped", "(SIGTERM)"], bold_first=False)
    s.arrow([(450, 332), (450, 356), (293, 356), (293, 380)])
    s.text(371, 349, "no", fit=24)
    s.box(200, 380, 186, 52, ["send SIGKILL, then poll", "up to 5 s for status", "= exited"], bold_first=False)
    s.box(428, 450, 184, 46, ["status = exited?"], rx=20, bold_first=False)
    s.arrow([(293, 432), (293, 473), (428, 473)])
    s.arrow([(580, 496), (580, 520)])
    s.text(588, 514, "yes", anchor="start", fit=30)
    s.box(500, 520, 112, 44, ["stopped", "(SIGKILL)"], bold_first=False)
    s.arrow([(445, 496), (445, 542), (420, 542)])
    s.text(439, 516, "no", anchor="end", fit=30)
    s.box(300, 522, 120, 40, ["error message"], bold_first=False)
    s.h = 576
    return s.render()


# ------------------------------------------------------------------ (e) path of a web-interface request

def ui_request_path():
    """The order of the checks follows ui/app.py: before_request (Host, token), then the route
    (JSON body, validation) and ui/minibox_client.py (limit of running containers, argument list)."""
    s = Svg(560, "Path of a request through the checks of the web interface",
            "A request from the browser to 127.0.0.1 passes the Host header check, the token check, the "
            "JSON body and size check, input validation and the limit of ten running containers; each check "
            "can reject it. Only then does minibox_client.py call the minibox command with an argument list.")
    lx, lw, rx, rw, bh, gap = 8, 330, 408, 204, 46, 22
    steps = [
        (["Request from the browser", "server listens on 127.0.0.1 only"], None),
        (["1  Host header check", "127.0.0.1:<port> or localhost:<port>"], ["400 bad_host", "(DNS rebinding)"]),
        (["2  Token check, paths /api/*", "hmac.compare_digest, constant time"], ["401 unauthorized"]),
        (["3  Body: JSON only, 16 KB cap", "(POST routes with a body)"], ["400 not JSON", "413 body too large"]),
        (["4  Input validation", "validation.py, strict rules per field"], ["400 invalid_input"]),
        (["5  At most 10 running containers", "checked in minibox_client.py"], ["409 limit reached"]),
        (["6  minibox_client.py", "argument list, no shell, rootfs fixed"], None),
        (["minibox command (root)", "run, ps, logs, stop, rm, exec"], None),
    ]
    y = 12
    for i, (lines, reject) in enumerate(steps):
        s.box(lx, y, lw, bh, lines, fill=PALE if i in (0, 7) else WHITE, bold_first=True)
        if reject:
            s.box(rx, y + (bh - (len(reject) * LINE + 14)) / 2, rw, len(reject) * LINE + 14, reject,
                  dash=True, bold_first=False)
            s.arrow([(lx + lw, y + bh / 2), (rx, y + bh / 2)], dash=True)
            s.text((lx + lw + rx) / 2, y + bh / 2 - 5, "no", fit=40)
        if i < len(steps) - 1:
            s.arrow([(lx + lw / 2, y + bh), (lx + lw / 2, y + bh + gap)])
            if i > 0:
                s.text(lx + lw / 2 + 8, y + bh + gap - 6, "ok", anchor="start", fit=30)
        y += bh + gap
    s.h = y - gap + 12
    return s.render()


BUILDERS = {"architecture": architecture, "startup_sequence": startup_sequence,
            "cgroup_hierarchy": cgroup_hierarchy, "lifecycle": lifecycle,
            "ui_request_path": ui_request_path}


def write_all(out_dir, chart_source=CHART_SOURCE):
    """Write the diagrams and copy the benchmark charts to <out_dir>/ (created); returns the list of paths."""
    out = assert_outside_repo(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in FIGURES:
        target = assert_outside_repo(out / (name + ".svg"))
        target.write_text(BUILDERS[name](), encoding="utf-8")
        paths.append(target)
    for name in CHARTS:
        src = chart_source / (name + ".svg")
        if not src.exists():
            raise ValueError("benchmark chart not found: %s (run bench/make_charts.py)" % src)
        target = assert_outside_repo(out / (name + ".svg"))
        target.write_text(flatten_svg(src.read_text(encoding="utf-8")), encoding="utf-8")   # light theme, no CSS variables
        paths.append(target)
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT / "report" / "figures"),
                    help="output folder (default ~/minibox-deliverables/report/figures; never inside the repo)")
    args = ap.parse_args(argv)
    try:
        for p in write_all(args.out):
            print("wrote %s (%d KB)" % (p, max(1, p.stat().st_size // 1024)))
    except RepoWriteError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
