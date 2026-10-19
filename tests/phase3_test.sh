#!/usr/bin/env bash
# phase3_test.sh - checks cgroup v2 limits (memory, cpu, pids), stats and cleanup.
# Usage: sudo tests/phase3_test.sh      (run from anywhere; takes ~20 s)
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo tests/phase3_test.sh"
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
command -v timeout >/dev/null 2>&1 || { echo "host tool 'timeout' missing"; exit 1; }

report() { # report <name> <0 if pass> [detail]
    if [ "$2" -eq 0 ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 ${3:+($3)}"
        FAILS=$((FAILS + 1))
    fi
}

# mb [options] -- command...: run a container, stdin closed.
mb() {
    "$MB" run --rootfs "$ROOTFS" "$@" </dev/null
}

leaf_count() {
    ls -d "$CG_BASE"/box-* 2>/dev/null | wc -l
}

# Print the path of the (single) container leaf once the PID is in it.
wait_for_leaf() {
    local i d
    for i in $(seq 1 50); do
        d=$(ls -d "$CG_BASE"/box-* 2>/dev/null | head -n 1)
        if [ -n "$d" ] && [ -n "$(cat "$d/cgroup.procs" 2>/dev/null)" ]; then
            echo "$d"
            return 0
        fi
        sleep 0.1
    done
    return 1
}

check_no_leftovers() { # check_no_leftovers <situation>
    [ "$(leaf_count)" -eq 0 ]
    report "no leftover box-* cgroup after $1" $? "$(ls -d "$CG_BASE"/box-* 2>/dev/null | tr '\n' ' ')"
}

if [ "$(leaf_count)" -ne 0 ]; then
    echo "Leftover cgroups from an earlier run exist:"
    ls -d "$CG_BASE"/box-*
    echo "Remove them first with: sudo rmdir $CG_BASE/box-*"
    exit 1
fi

# ---- 1. limit files are written correctly ----
"$MB" run --rootfs "$ROOTFS" --memory 64M --cpus 0.5 --pids 32 -- sleep 5 \
    </dev/null >/dev/null 2>&1 &
bg=$!
leaf=$(wait_for_leaf)
if [ -n "$leaf" ]; then
    v=$(cat "$leaf/memory.max");  [ "$v" = "67108864" ];      report "memory.max is 67108864" $? "got '$v'"
    v=$(cat "$leaf/cpu.max");     [ "$v" = "50000 100000" ];  report "cpu.max is '50000 100000'" $? "got '$v'"
    v=$(cat "$leaf/pids.max");    [ "$v" = "32" ];            report "pids.max is 32" $? "got '$v'"
    v=$(cat "$leaf/memory.swap.max" 2>/dev/null); [ -z "$v" ] || [ "$v" = "0" ]
    report "memory.swap.max is 0" $? "got '$v'"
else
    report "leaf cgroup box-* appeared with the container in it" 1
fi
wait "$bg"
code=$?
[ "$code" -eq 0 ]
report "limited container exits normally" $? "exit $code"
check_no_leftovers "normal exit"

# ---- 2. memory limit: OOM kill ----
out=$(timeout 60 "$MB" run --rootfs "$ROOTFS" --memory 32M -- \
      /bin/sh -c 'x=aaaaaaaa; while :; do x=$x$x; done' </dev/null 2>&1)
code=$?
[ "$code" -eq 137 ]
report "memory hog with --memory 32M exits 137" $? "exit $code"
echo "$out" | grep -q "OOM-killed"
report "OOM message printed" $? "output: $(echo "$out" | tail -n 3 | tr '\n' ' ')"
check_no_leftovers "OOM kill"

# ---- 3. pids limit: fork loop is capped ----
# The inner sh starts the sleeps (it may die with "can't fork"); the outer sh
# (PID 1) then counts processes with a /proc glob, which needs no fork.
out=$(mb --pids 20 --stats -- /bin/sh -c \
      'sh -c "for i in \$(seq 1 100); do sleep 30 & done" 2>/dev/null; set -- /proc/[0-9]*; echo $#' 2>&1)
count=$(echo "$out" | grep -E '^[0-9]+$' | tail -n 1)
peak=$(echo "$out" | awk '/\[stats\] pids.peak/ {print $3}')
[ -n "$count" ] && [ "$count" -ge 2 ] && [ "$count" -le 20 ]
report "--pids 20: at most 20 processes running" $? "counted '$count'"
[ -n "$peak" ] && [ "$peak" -le 20 ]
report "--pids 20: pids.peak <= 20" $? "pids.peak '$peak'"
check_no_leftovers "pids test"

# ---- 4. cpu limit: ~50% of one core ----
# timeout must not be PID 1: busybox timeout execs the command in its own
# process, and PID 1 of a namespace ignores SIGTERM unless it has a handler.
start=$(date +%s%N)
out=$(timeout 30 "$MB" run --rootfs "$ROOTFS" --cpus 0.5 --stats -- \
      /bin/sh -c 'timeout 3 sh -c "while :; do :; done"; true' </dev/null 2>&1)
end=$(date +%s%N)
usage=$(echo "$out" | awk '/\[stats\] cpu.usage_usec/ {print $3}')
elapsed_us=$(( (end - start) / 1000 ))
if [ -n "$usage" ] && [ "$elapsed_us" -lt 10000000 ]; then
    pct=$(( usage * 100 / elapsed_us ))
    [ "$pct" -ge 35 ] && [ "$pct" -le 65 ]
    report "--cpus 0.5: busy loop used ${pct}% of one core (want 35-65%)" $? \
           "usage_usec=$usage elapsed_us=$elapsed_us"
else
    report "--cpus 0.5: busy loop finished with stats" 1 "usage='$usage' elapsed_us=$elapsed_us"
fi
check_no_leftovers "cpu test"

# ---- 5. failing command ----
mb --memory 64M -- nosuchcmd >/dev/null 2>&1
code=$?
[ "$code" -eq 127 ]
report "failing command with limits exits 127" $? "exit $code"
check_no_leftovers "failing command"

# ---- 6. SIGTERM to minibox kills the container and cleans up ----
"$MB" run --rootfs "$ROOTFS" --pids 32 -- sleep 30 </dev/null >/dev/null 2>&1 &
bg=$!
if wait_for_leaf >/dev/null; then
    kill -TERM "$bg"
    wait "$bg"
    code=$?
    [ "$code" -eq 137 ]
    report "SIGTERM to minibox kills the container (exit 137)" $? "exit $code"
else
    kill -TERM "$bg" 2>/dev/null; wait "$bg"
    report "SIGTERM test: leaf cgroup appeared" 1
fi
check_no_leftovers "SIGTERM"

# ---- 7. no limit flags: works, no cgroup ----
out=$(mb -- cat /proc/self/cgroup)
code=$?
[ "$code" -eq 0 ] && ! echo "$out" | grep -q '/minibox/'
report "container without limits works and is not in a minibox cgroup" $? "exit $code, cgroup '$out'"
[ "$(leaf_count)" -eq 0 ]
report "container without limits created no box-* cgroup" $?
out=$(mb --pids 32 -- cat /proc/self/cgroup)
echo "$out" | grep -q '/minibox/box-'
report "container with limits runs inside /minibox/box-<id>" $? "cgroup '$out'"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
