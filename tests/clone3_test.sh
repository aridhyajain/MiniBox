#!/usr/bin/env bash
# clone3_test.sh - checks that a container with limits is created INSIDE its cgroup (clone3 with
# CLONE_INTO_CGROUP) and that the old path (clone() + write to cgroup.procs, forced with
# MINIBOX_NO_CLONE3=1) still works and enforces the same limits.
# Usage: sudo bash tests/clone3_test.sh     (run from anywhere; takes about 1 minute)
# Uses a temporary state directory (MINIBOX_STATE_DIR) that is removed at the end.
# Every minibox call goes through `timeout`, so a hang becomes a FAIL instead of a stuck test.
# INFO lines (the timing comparison) never fail the script.
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0
TIMING_RUNS=20

# shellcheck source=lib.sh
. "$ROOT_DIR/tests/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo bash tests/clone3_test.sh"
    exit 1
fi
if [ ! -x "$MB" ]; then
    echo "$MB not found; run 'make' first."
    exit 1
fi
if [ ! -x "$ROOTFS/bin/busybox" ]; then
    echo "Alpine rootfs missing; run 'make rootfs' first."
    exit 1
fi
for tool in ip timeout awk sort; do
    command -v "$tool" >/dev/null 2>&1 || { echo "host tool '$tool' missing"; exit 1; }
done

veth_count() {
    ip -o link show | grep -c 'vethh'
}

leaf_count() {
    ls -d "$CG_BASE"/box-* 2>/dev/null | wc -l
}

if [ "$(leaf_count)" -ne 0 ] || [ "$(veth_count)" -ne 0 ]; then
    echo "Leftover box-* cgroups or vethh* interfaces exist (earlier run?). Remove them first:"
    ls -d "$CG_BASE"/box-* 2>/dev/null
    ip -o link show | grep vethh
    exit 1
fi

STATE=$(mktemp -d)
FAKE_ROOTFS=$(mktemp -d)
TRACE=$(mktemp)
export MINIBOX_STATE_DIR="$STATE"

# Remove whatever is still there (even after a failed check or Ctrl-C), then the temp files.
cleanup() {
    local id
    for id in $(timeout 20 "$MB" ps -a 2>/dev/null | awk 'NR>1 {print $1}'); do
        timeout 20 "$MB" rm -f "$id" >/dev/null 2>&1
    done
    rm -rf "$STATE" "$FAKE_ROOTFS" "$TRACE"
}
trap cleanup EXIT

report() { # report <name> <0 if pass> [detail]
    if [ "$2" -eq 0 ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 ${3:+($3)}"
        FAILS=$((FAILS + 1))
    fi
}

# run_mode <mode> <seconds> command...: run a command under `timeout`; mode "clone3" makes sure the
# diagnostic switch is unset, mode "migrate" sets MINIBOX_NO_CLONE3=1 (the old cgroup.procs path).
run_mode() {
    local mode=$1 secs=$2
    shift 2
    if [ "$mode" = migrate ]; then
        env MINIBOX_NO_CLONE3=1 timeout "$secs" "$@"
    else
        env -u MINIBOX_NO_CLONE3 timeout "$secs" "$@"
    fi
}

# mbr <mode> <rootfs> <seconds> [run options] -- command...: foreground container, stdin closed.
mbr() {
    local mode=$1 rootfs=$2 secs=$3
    shift 3
    run_mode "$mode" "$secs" "$MB" run --rootfs "$rootfs" "$@" </dev/null
}

# run_d <mode> [options] -- command...: detached container; prints the ID.
run_d() {
    local mode=$1
    shift
    run_mode "$mode" 30 "$MB" run -d --rootfs "$ROOTFS" "$@" </dev/null
}

# field <id> <key>: one value from the container's state file.
field() {
    sed -n "s/^$2=//p" "$STATE/$1/state"
}

# wait_running <id> <seconds>: poll the state file until the container has a PID and is running.
wait_running() {
    local i
    for i in $(seq 1 $(($2 * 10))); do
        [ "$(field "$1" status)" = running ] && [ -n "$(field "$1" pid)" ] && return 0
        sleep 0.1
    done
    return 1
}

# wait_clean <seconds>: poll until no box-* cgroup and no vethh* interface exist.
wait_clean() {
    local i
    for i in $(seq 1 $(($1 * 10))); do
        [ "$(leaf_count)" -eq 0 ] && [ "$(veth_count)" -eq 0 ] && return 0
        sleep 0.1
    done
    return 1
}

# state_rows: number of containers listed by `ps -a` (header not counted).
state_rows() {
    timeout 20 "$MB" ps -a 2>/dev/null | awk 'NR>1' | wc -l
}

check_clean() { # check_clean <situation>
    wait_clean 5
    report "no box-* cgroup and no veth left after $1" $? \
           "cgroups: $(ls -d "$CG_BASE"/box-* 2>/dev/null | tr '\n' ' ') veths: $(veth_count)"
}

# ---------------------------------------------------------------------------------------------
# Checks that must hold in BOTH modes: the container is in its cgroup and the limits enforce.
# ---------------------------------------------------------------------------------------------
mode_checks() { # mode_checks <mode>
    local mode=$1 id pid v out code count peak start end usage elapsed_us pct leaked

    echo "---- mode: $mode ----"

    # 1. the container's init is listed in cgroup.procs; the limit files hold the values
    id=$(run_d "$mode" --memory 64M --cpus 0.5 --pids 32 -- sleep 30)
    if wait_running "$id" 10; then
        pid=$(field "$id" pid)
        grep -qx "$pid" "$CG_BASE/box-$id/cgroup.procs"
        report "[$mode] init PID $pid is listed in box-$id/cgroup.procs" $? \
               "procs: $(tr '\n' ' ' < "$CG_BASE/box-$id/cgroup.procs" 2>/dev/null)"
        v=$(cat "$CG_BASE/box-$id/memory.max");  [ "$v" = 67108864 ]
        report "[$mode] memory.max is 67108864" $? "got '$v'"
        v=$(cat "$CG_BASE/box-$id/cpu.max");     [ "$v" = "50000 100000" ]
        report "[$mode] cpu.max is '50000 100000'" $? "got '$v'"
        v=$(cat "$CG_BASE/box-$id/pids.max");    [ "$v" = 32 ]
        report "[$mode] pids.max is 32" $? "got '$v'"
        # no descriptor of the cgroup directory may leak into the container
        leaked=$(readlink /proc/"$pid"/fd/* 2>/dev/null | grep -c '/sys/fs/cgroup')
        [ "$leaked" -eq 0 ]
        report "[$mode] no cgroup directory descriptor open in the container" $? "$leaked open"
    else
        report "[$mode] detached container with limits is running" 1 "id '$id'"
    fi
    timeout 30 "$MB" rm -f "$id" >/dev/null 2>&1
    check_clean "[$mode] detached run"

    # 1b. --init: its PID 1 never execs, so a leaked descriptor would stay open there
    id=$(run_d "$mode" --init --memory 64M --pids 32 -- sleep 30)
    if wait_running "$id" 10; then
        pid=$(field "$id" pid)
        grep -qx "$pid" "$CG_BASE/box-$id/cgroup.procs"
        report "[$mode] --init: PID 1 (minibox-init) is listed in cgroup.procs" $?
        leaked=$(readlink /proc/"$pid"/fd/* 2>/dev/null | grep -c '/sys/fs/cgroup')
        [ "$leaked" -eq 0 ]
        report "[$mode] --init: no cgroup directory descriptor open in PID 1" $? "$leaked open"
    else
        report "[$mode] detached --init container with limits is running" 1 "id '$id'"
    fi
    timeout 30 "$MB" rm -f "$id" >/dev/null 2>&1
    check_clean "[$mode] --init run"

    # 2. no "clone3 unavailable" note in clone3 mode; none in migrate mode either (it is requested)
    out=$(mbr "$mode" "$ROOTFS" 30 --memory 64M -- /bin/true 2>&1)
    code=$?
    [ "$code" -eq 0 ] && ! echo "$out" | grep -q 'clone3 unavailable'
    report "[$mode] exits 0 without a clone3 fallback note" $? "exit $code, output '$out'"

    # 3. memory limit: OOM kill
    out=$(mbr "$mode" "$ROOTFS" 60 --memory 32M -- \
          /bin/sh -c 'x=aaaaaaaa; while :; do x=$x$x; done' 2>&1)
    code=$?
    [ "$code" -eq 137 ]
    report "[$mode] memory hog with --memory 32M exits 137" $? "exit $code"
    echo "$out" | grep -q "OOM-killed"
    report "[$mode] OOM message printed" $? "output: $(echo "$out" | tail -n 3 | tr '\n' ' ')"
    check_clean "[$mode] OOM kill"

    # 4. pids limit: fork loop is capped (same command as phase3_test.sh)
    out=$(mbr "$mode" "$ROOTFS" 60 --pids 20 --stats -- /bin/sh -c \
          'sh -c "for i in \$(seq 1 100); do sleep 30 & done" 2>/dev/null; set -- /proc/[0-9]*; echo $#' 2>&1)
    count=$(echo "$out" | grep -E '^[0-9]+$' | tail -n 1)
    peak=$(echo "$out" | awk '/\[stats\] pids.peak/ {print $3}')
    [ -n "$count" ] && [ "$count" -ge 2 ] && [ "$count" -le 20 ]
    report "[$mode] --pids 20: at most 20 processes running" $? "counted '$count'"
    [ -n "$peak" ] && [ "$peak" -le 20 ]
    report "[$mode] --pids 20: pids.peak <= 20" $? "pids.peak '$peak'"
    check_clean "[$mode] pids test"

    # 5. cpu limit: about 50% of one core (timeout must not be PID 1, see phase3_test.sh)
    start=$(date +%s%N)
    out=$(mbr "$mode" "$ROOTFS" 30 --cpus 0.5 --stats -- \
          /bin/sh -c 'timeout 3 sh -c "while :; do :; done"; true' 2>&1)
    end=$(date +%s%N)
    usage=$(echo "$out" | awk '/\[stats\] cpu.usage_usec/ {print $3}')
    elapsed_us=$(( (end - start) / 1000 ))
    if [ -n "$usage" ] && [ "$elapsed_us" -lt 10000000 ]; then
        pct=$(( usage * 100 / elapsed_us ))
        [ "$pct" -ge 35 ] && [ "$pct" -le 65 ]
        report "[$mode] --cpus 0.5: busy loop used ${pct}% of one core (want 35-65%)" $? \
               "usage_usec=$usage elapsed_us=$elapsed_us"
    else
        report "[$mode] --cpus 0.5: busy loop finished with stats" 1 "usage='$usage' elapsed_us=$elapsed_us"
    fi
    check_clean "[$mode] cpu test"
}

mode_checks clone3
mode_checks migrate

# ---------------------------------------------------------------------------------------------
# strace-based checks (skipped if strace is missing or too old for -e inject).
# ---------------------------------------------------------------------------------------------
echo "---- strace: which system call creates the container, and the fallback ----"
if command -v strace >/dev/null 2>&1 &&
   timeout 20 strace -qq -o /dev/null -e inject=getpid:error=ENOSYS true >/dev/null 2>&1; then

    run_mode clone3 60 strace -f -qq -o "$TRACE" -e trace=clone3 \
        "$MB" run --rootfs "$ROOTFS" --memory 64M -- /bin/true </dev/null >/dev/null 2>&1
    grep -Eq 'CLONE_INTO_CGROUP|cgroup=' "$TRACE"
    report "strace: clone3 with CLONE_INTO_CGROUP is used by default" $? "trace: $(head -c 300 "$TRACE")"
    check_clean "strace clone3 run"

    run_mode migrate 60 strace -f -qq -o "$TRACE" -e trace=clone3 \
        "$MB" run --rootfs "$ROOTFS" --memory 64M -- /bin/true </dev/null >/dev/null 2>&1
    ! grep -Eq 'CLONE_INTO_CGROUP|cgroup=' "$TRACE"
    report "strace: MINIBOX_NO_CLONE3=1 does not use CLONE_INTO_CGROUP" $? "trace: $(head -c 300 "$TRACE")"
    check_clean "strace migrate run"

    # Make the kernel refuse clone3 (like an old kernel or a seccomp filter would) and check that
    # minibox falls back, the container still lands in its cgroup, and nothing is left behind.
    for err in ENOSYS EPERM; do
        out=$(run_mode clone3 60 strace -f -qq -o "$TRACE" -e trace=clone3 -e "inject=clone3:error=$err" \
              "$MB" run --rootfs "$ROOTFS" --memory 64M -- cat /proc/self/cgroup </dev/null 2>&1)
        code=$?
        [ "$code" -eq 0 ] && echo "$out" | grep -q 'clone3 unavailable' && echo "$out" | grep -q '/minibox/box-'
        report "clone3 fails with $err: fallback note, container runs inside box-<id>" $? \
               "exit $code, output '$out'"
        check_clean "clone3 $err injection"
    done
else
    echo "SKIP: strace missing or without '-e inject' support (install strace to run these checks)"
fi

# ---------------------------------------------------------------------------------------------
# Failure injection: errors must be clear and leave no cgroup, veth or state behind.
# ---------------------------------------------------------------------------------------------
echo "---- failure injection ----"
for bad in "--memory abc" "--memory 0" "--cpus 0" "--pids 0"; do
    # shellcheck disable=SC2086
    out=$(mbr clone3 "$ROOTFS" 20 $bad -- /bin/true 2>&1)
    code=$?
    [ "$code" -ne 0 ] && echo "$out" | grep -q '^minibox:'
    report "invalid option '$bad' is rejected with a clear message" $? "exit $code, output '$out'"
done
check_clean "invalid options"

# A directory that passes the "looks like Alpine" test (bin/busybox exists) but is not a real
# root filesystem: the container fails after its cgroup (and veth) were already prepared.
mkdir -p "$FAKE_ROOTFS/bin"
: > "$FAKE_ROOTFS/bin/busybox"
for mode in clone3 migrate; do
    out=$(mbr "$mode" "$FAKE_ROOTFS" 30 --memory 64M --pids 16 -- /bin/true 2>&1)
    code=$?
    [ "$code" -ne 0 ] && [ -n "$out" ]
    report "[$mode] fake rootfs with limits: non-zero exit and a message" $? "exit $code, output '$out'"
    check_clean "[$mode] fake rootfs"

    out=$(mbr "$mode" "$FAKE_ROOTFS" 30 --net veth --memory 64M -- /bin/true 2>&1)
    code=$?
    [ "$code" -ne 0 ] && [ -n "$out" ]
    report "[$mode] fake rootfs with --net veth and limits: non-zero exit and a message" $? \
           "exit $code, output '$out'"
    check_clean "[$mode] fake rootfs with veth"
done
[ "$(state_rows)" -eq 0 ]
report "failed starts left no container state behind" $? "ps -a rows: $(state_rows)"

# ---------------------------------------------------------------------------------------------
# Informational timing (never fails), same measurement as bench/bench.py. --net none is explicit (and printed below): the default is also none, but a veth
# setup (its floor is far above the cgroup cost, see docs/BENCHMARKS.md) would hide the difference.
# ---------------------------------------------------------------------------------------------
echo "---- timing (information only) ----"
# Same measurement as bench/bench.py (timed(): perf_counter_ns around subprocess.run, stdin/stdout/
# stderr to /dev/null, foreground run, same limits as LIMITS_FOR_STARTUP). One python3 process does
# the whole loop, so no date/env/timeout/subshell is inside the timed region and there is no sleep or
# polling. 3 warm-up runs per mode are discarded; the modes alternate to spread drift over both.
# The whole python3 helper runs under `timeout`. For the full benchmark see bench/bench.py.
TIMING_WARMUPS=3
TIMING_CMD=("$MB" run --rootfs "$ROOTFS" --net none --memory 256M --cpus 1 --pids 64 -- /bin/true)
echo "INFO: command: ${TIMING_CMD[*]}"
echo "INFO: migrate mode adds MINIBOX_NO_CLONE3=1 to the environment; $TIMING_WARMUPS warm-ups discarded, $TIMING_RUNS runs per mode, interleaved"
timing_py='
import os, subprocess, sys, time
runs, warm = int(sys.argv[1]), int(sys.argv[2])
cmd = sys.argv[3:]
base = {k: v for k, v in os.environ.items() if k != "MINIBOX_NO_CLONE3"}
envs = {"clone3": base, "migrate": dict(base, MINIBOX_NO_CLONE3="1")}
def one(mode):
    t0 = time.perf_counter_ns()
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, env=envs[mode], timeout=20)
    return (time.perf_counter_ns() - t0) / 1e6
for _ in range(warm):
    for m in envs: one(m)
res = {m: [] for m in envs}
for _ in range(runs):
    for m in envs: res[m].append(one(m))
def pct(v, q):
    v = sorted(v); i = (len(v) - 1) * q; lo = int(i)
    return v[lo] + (v[min(lo + 1, len(v) - 1)] - v[lo]) * (i - lo)
for m, v in res.items():
    print("%s %.1f %.1f %.1f" % (m, pct(v, 0.5), min(v), pct(v, 0.95)))
'
if out=$(timeout 300 python3 -I -c "$timing_py" "$TIMING_RUNS" "$TIMING_WARMUPS" "${TIMING_CMD[@]}" 2>&1); then
    printf 'INFO: %-34s %10s %10s %10s\n' "mode ($TIMING_RUNS runs)" "median ms" "min ms" "p95 ms"
    printf '%s\n' "$out" | awk '{ printf "INFO: %-34s %10s %10s %10s\n", ($1 == "clone3" ? "clone3 into the cgroup" : "clone() + cgroup.procs (migrate)"), $2, $3, $4 }'
    printf '%s\n' "$out" | awk '
        $1 == "clone3" { c = $2 } $1 == "migrate" { m = $2 }
        END { d = m - c; if (d < 0) d = -d
              if (d < 1) print "INFO: the two modes look identical (medians differ by under 1 ms); not tuned."
              else printf "INFO: medians differ by %.1f ms.\n", m - c }'
else
    echo "INFO: timing not available (helper failed or timed out): $out"
fi

# ---------------------------------------------------------------------------------------------
# Nothing may be left at the end.
# ---------------------------------------------------------------------------------------------
echo "---- leftovers ----"
wait_clean 5
report "no box-* cgroup and no veth left at the end" $? \
       "cgroups: $(ls -d "$CG_BASE"/box-* 2>/dev/null | tr '\n' ' ') veths: $(veth_count)"
[ "$(state_rows)" -eq 0 ]
report "no container state left at the end" $? "ps -a rows: $(state_rows)"
left=$(state_clean "$STATE")
report "state directory holds nothing but .lock" $? "found: $left"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
