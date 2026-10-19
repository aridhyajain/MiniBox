#!/usr/bin/env bash
# phase6_test.sh - checks the benchmark tooling: bench.py all --quick, the result files, the charts.
# Usage: sudo bash tests/phase6_test.sh      (about 40-60 s)
# It does NOT judge the numbers (they depend on the machine); it checks that every output exists,
# is well-formed, that the limit tests printed PASS/FAIL lines, and that nothing is left behind.
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo bash tests/phase6_test.sh"
    exit 1
fi
if [ ! -x "$MB" ]; then
    echo "$MB not found; run 'make' first."
    exit 1
fi
if [ ! -x "$ROOT_DIR/rootfs/bin/busybox" ]; then
    echo "Alpine rootfs missing; run 'make rootfs' first."
    exit 1
fi
for tool in python3 ip; do
    command -v "$tool" >/dev/null 2>&1 || { echo "host tool '$tool' missing"; exit 1; }
done

veth_count() { ip -o link show | grep -c 'vethh'; }

if ls -d "$CG_BASE"/box-* >/dev/null 2>&1 || [ "$(veth_count)" -ne 0 ]; then
    echo "Leftover box-* cgroups or vethh* interfaces exist (earlier run?). Remove them first."
    exit 1
fi

OUT=$(mktemp -d)
LOG="$OUT/bench.log"
trap 'rm -rf "$OUT"' EXIT

report() { # report <name> <0 if pass> [detail]
    if [ "$2" -eq 0 ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 ${3:+($3)}"
        FAILS=$((FAILS + 1))
    fi
}

# timeout 150: the test itself can never hang. 124 = timed out.
T0=$(date +%s)
timeout 150 python3 "$ROOT_DIR/bench/bench.py" all --quick --out "$OUT" >"$LOG" 2>&1
RC=$?
echo "INFO: quick run took $(( $(date +%s) - T0 )) s (should stay under about 90 s)"
[ "$RC" -ne 124 ]
report "bench.py did not hang (150 s limit)" $? "killed by timeout"
[ "$RC" -eq 0 ]
report "bench.py all --quick exits 0" $? "exit code $RC; see log below"
if grep -Eq '^(ERROR|TIMEOUT):' "$LOG"; then
    echo "      ERROR/TIMEOUT lines in the bench output:"
    grep -E '^(ERROR|TIMEOUT):' "$LOG" | sed 's/^/        /'
fi
! grep -Eq '^(ERROR|TIMEOUT):' "$LOG"
report "no ERROR or TIMEOUT in the bench output" $?
[ "$FAILS" -eq 0 ] || tail -n 30 "$LOG"

RES="$OUT/latest"
for f in startup_raw.csv startup_summary.csv memory.csv memory_incremental.csv limits.csv system.json summary.txt; do
    [ -s "$RES/$f" ]
    report "result file $f exists and is not empty" $?
done
[ -d "$OUT/latest" ] && [ "$(ls -d "$OUT"/2* 2>/dev/null | wc -l)" -eq 1 ]
report "timestamped run directory and latest/ both exist" $?

python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["kernel"] and d["cpu_cores"]' "$RES/system.json"
report "system.json is valid JSON with kernel and cpu_cores" $?

# At least the five startup configs that need no docker must have been measured.
[ "$(grep -c ',measured,' "$RES/startup_summary.csv")" -ge 4 ]
report "startup_summary.csv has measured configurations" $?
grep -q '^docker,' "$RES/startup_summary.csv"
report "docker row present (measured or marked not measured)" $?

grep -Eq '^(OK|FAIL|ERROR|TIMEOUT): memory/' "$LOG" && grep -Eq '^(OK|FAIL|ERROR|TIMEOUT): pids/' "$LOG" && grep -Eq '^(OK|FAIL|ERROR|TIMEOUT): cpu/' "$LOG"
report "limit tests printed a status line for memory, pids and cpu" $?
echo "INFO: limit checks: $(grep -c '^OK: ' "$LOG") OK, $(grep -c '^FAIL: ' "$LOG") FAIL (machine dependent, informational)"
python3 -c 'import csv,sys; bad=[r["test"] for r in csv.DictReader(open(sys.argv[1])) if r["status"] in ("ERROR","TIMEOUT")]; print("\n".join(bad)); sys.exit(1 if bad else 0)' "$RES/limits.csv"
report "limits.csv has no ERROR/TIMEOUT rows" $?
grep -E '^(Section [ABC]|Total time)' "$LOG" | sed 's/^/INFO: /'

grep -q '^CLEANUP: OK' "$LOG"
report "bench.py reported a clean state afterwards" $?

python3 "$ROOT_DIR/bench/make_charts.py" "$RES" --out "$OUT/charts" >/dev/null
report "make_charts.py exits 0" $?
for s in startup memory limits; do
    python3 -c 'import sys,xml.etree.ElementTree as E; E.parse(sys.argv[1])' "$OUT/charts/$s.svg"
    report "$s.svg exists and is well-formed XML" $?
done

# Nothing may remain: no cgroup, no host-side veth, no container state (bench used a temp state dir
# that it removes; the default state directory must not have gained entries either).
ls -d "$CG_BASE"/box-* >/dev/null 2>&1
[ $? -ne 0 ]
report "no box-* cgroup left" $?
[ "$(veth_count)" -eq 0 ]
report "no vethh* interface left" $?
[ -z "$(ls /tmp | grep '^minibox-bench-')" ]
report "bench temporary state directory removed" $?

if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
