#!/usr/bin/env bash
# phase4_test.sh - checks container management: run -d, ps, logs, stop, rm.
# Usage: sudo tests/phase4_test.sh      (run from anywhere; takes ~1 minute)
# Uses a temporary state directory (MINIBOX_STATE_DIR) that is removed at the end.
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo tests/phase4_test.sh"
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
for tool in ip ping timeout python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "host tool '$tool' missing"; exit 1; }
done

veth_count() {
    ip -o link show | grep -c 'vethh'
}

if ls -d "$CG_BASE"/box-* >/dev/null 2>&1 || [ "$(veth_count)" -ne 0 ]; then
    echo "Leftover box-* cgroups or vethh* interfaces exist (earlier run?). Remove them first:"
    ls -d "$CG_BASE"/box-* 2>/dev/null
    ip -o link show | grep vethh
    exit 1
fi

STATE=$(mktemp -d)
JSON_TMP=$(mktemp)
export MINIBOX_STATE_DIR="$STATE"

# Remove whatever is still there (even after a failed check or Ctrl-C), then the temp dirs.
cleanup() {
    local id
    for id in $("$MB" ps -a 2>/dev/null | awk 'NR>1 {print $1}'); do
        "$MB" rm -f "$id" >/dev/null 2>&1
    done
    rm -rf "$STATE" "$JSON_TMP"
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

# run_d [options] -- command...: start a detached container, stdin closed.
run_d() {
    "$MB" run -d --rootfs "$ROOTFS" "$@" </dev/null
}

# field <id> <key>: one value from the container's state file.
field() {
    sed -n "s/^$2=//p" "$STATE/$1/state"
}

# wait_status <id> <status> <seconds>: poll the state file.
wait_status() {
    local i
    for i in $(seq 1 $(($3 * 10))); do
        [ "$(field "$1" status)" = "$2" ] && return 0
        sleep 0.1
    done
    return 1
}

# The kernel removes the veth a moment after the container's netns dies.
wait_no_veth() {
    local i
    for i in $(seq 1 30); do
        [ "$(veth_count)" -eq 0 ] && return 0
        sleep 0.1
    done
    return 1
}

# wait_log <id> <text> <seconds>: poll the log for a string.
wait_log() {
    local i
    for i in $(seq 1 $(($3 * 10))); do
        "$MB" logs "$1" 2>/dev/null | grep -q "$2" && return 0
        sleep 0.1
    done
    return 1
}

# ---- 0. nothing yet: empty JSON list ----
out=$("$MB" ps --json)
[ "$out" = "[]" ]
report "ps --json with no containers prints []" $? "got '$out'"

# ---- 1. run -d, ps, ps -a ----
id=$(run_d -- sh -c 'sleep 3')
echo "$id" | grep -Eq '^[0-9a-f]{12}$'
report "run -d prints only a 12-hex ID" $? "got '$id'"

row=$("$MB" ps | grep "^$id")
echo "$row" | grep -q running && echo "$row" | grep -q 'sleep 3'
report "ps shows it running with its command" $? "row: $row"

wait_status "$id" exited 10
report "container exits by itself (status exited)" $? "status '$(field "$id" status)'"
"$MB" ps | grep -q "^$id"
[ $? -ne 0 ]
report "ps (without -a) no longer lists it" $?
"$MB" ps -a | grep "^$id" | grep -q exited
report "ps -a lists it as exited" $?

# ---- 2. logs ----
id=$(run_d -- sh -c 'for i in 1 2 3; do echo line$i; sleep 1; done')
out=$(timeout 4 "$MB" logs -f "$id")
echo "$out" | grep -q line1
report "logs -f shows line1 while the container runs" $? "got: $(echo "$out" | tr '\n' ' ')"
wait_status "$id" exited 10
out=$("$MB" logs "$id")
echo "$out" | grep -q line1 && echo "$out" | grep -q line2 && echo "$out" | grep -q line3
report "logs after exit shows line1..line3" $? "got: $(echo "$out" | tr '\n' ' ')"

# ---- 3. stop: SIGTERM is ignored by PID 1, so SIGKILL ends it (exit 137) ----
id=$(run_d --pids 32 --net veth -- sleep 300)
[ -d "$CG_BASE/box-$id" ]
report "cgroup box-<id> exists while running" $?
[ "$(veth_count)" -eq 1 ]
report "one vethh* interface exists while running" $? "count $(veth_count)"

start=$(date +%s.%N)
out=$("$MB" stop --time 2 "$id")
end=$(date +%s.%N)
secs=$(awk -v s="$start" -v e="$end" 'BEGIN { printf "%.1f", e - s }')
awk -v t="$secs" 'BEGIN { exit !(t >= 2 && t <= 6) }'
report "stop --time 2 took between 2 and 6 s" $? "took ${secs}s"
echo "$out" | grep -q SIGKILL
report "stop reports that SIGKILL was needed" $? "output: $out"
[ "$(field "$id" status)" = exited ] && [ "$(field "$id" exit_code)" = 137 ]
report "stopped container is exited with exit code 137" $? \
       "status '$(field "$id" status)' code '$(field "$id" exit_code)'"
[ ! -d "$CG_BASE/box-$id" ]
report "no box-<id> cgroup left after stop" $?
[ "$(veth_count)" -eq 0 ]
report "no vethh* interface left after stop" $? "$(ip -o link show | grep vethh)"
"$MB" rm "$id" >/dev/null

# ---- 4. two veth containers at once: different subnets ----
a=$(run_d --net veth -- sh -c 'ping -c 1 -W 2 10.0.0.1 && echo PINGOK; sleep 300')
b=$(run_d --net veth -- sh -c 'ping -c 1 -W 2 10.0.1.1 && echo PINGOK; sleep 300')
ip_a=$(field "$a" ip)
ip_b=$(field "$b" ip)
[ "$ip_a" = "10.0.0.2" ] && [ "$ip_b" = "10.0.1.2" ]
report "two veth containers get 10.0.0.2 and 10.0.1.2" $? "got '$ip_a' and '$ip_b'"
wait_log "$a" PINGOK 8
report "container A can ping its host address 10.0.0.1" $? "log: $("$MB" logs "$a" | tr '\n' ' ')"
wait_log "$b" PINGOK 8
report "container B can ping its host address 10.0.1.1" $? "log: $("$MB" logs "$b" | tr '\n' ' ')"
[ "$(veth_count)" -eq 2 ]
report "two vethh* interfaces exist while both run" $? "count $(veth_count)"
"$MB" stop --time 1 "$a" >/dev/null
"$MB" stop --time 1 "$b" >/dev/null
[ "$(veth_count)" -eq 0 ]
report "both veth interfaces are gone after stopping both" $? "$(ip -o link show | grep vethh)"
c=$(run_d --net veth -- sleep 300)
[ "$(field "$c" ip)" = "10.0.0.2" ]
report "the lowest free subnet (10.0.0.x) is reused" $? "got '$(field "$c" ip)'"
"$MB" stop --time 1 "$c" >/dev/null
"$MB" rm "$a" >/dev/null; "$MB" rm "$b" >/dev/null; "$MB" rm "$c" >/dev/null

# ---- 5. names, rm, JSON ----
n=$(run_d --name web -- sleep 300)
[ "$(field "$n" name)" = web ]
report "--name web is stored" $? "got '$(field "$n" name)'"
# Earlier sections leave their exited containers listed, so compare before/after.
rows_before=$("$MB" ps -a | awk 'NR>1' | wc -l)
dirs_before=$(find "$STATE" -mindepth 1 -maxdepth 1 -type d | wc -l)
out=$(run_d --name web -- sleep 300 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "already used"
report "a duplicate --name is refused" $? "exit $code, output: $out"
rows_after=$("$MB" ps -a | awk 'NR>1' | wc -l)
dirs_after=$(find "$STATE" -mindepth 1 -maxdepth 1 -type d | wc -l)
[ "$rows_after" -eq "$rows_before" ] && [ "$dirs_after" -eq "$dirs_before" ]
report "the refused duplicate left no container behind" $? \
       "ps -a rows before $rows_before after $rows_after; state dirs before $dirs_before after $dirs_after"
"$MB" logs web >/dev/null 2>&1
report "logs works with the name" $?
"$MB" rm web >/dev/null 2>&1
[ $? -ne 0 ] && [ -d "$STATE/$n" ]
report "rm refuses a running container" $?
"$MB" stop --time 1 web >/dev/null
"$MB" rm web >/dev/null
[ $? -eq 0 ] && [ ! -d "$STATE/$n" ]
report "rm removes the state directory" $?

j=$(run_d -- sh -c 'echo "a\\b"')
wait_status "$j" exited 10
"$MB" ps -a --json >"$JSON_TMP"
python3 -m json.tool <"$JSON_TMP" >/dev/null 2>&1
report "ps -a --json is valid JSON" $?
python3 - "$j" "$JSON_TMP" <<'PYEOF'
import json, sys
want = 'sh -c echo "a' + chr(92) * 2 + 'b"'
items = json.load(open(sys.argv[2]))
sys.exit(0 if any(i["id"] == sys.argv[1] and i["command"] == want and i["status"] == "exited"
                  for i in items) else 1)
PYEOF
report "JSON escapes quotes and backslashes (command round-trips)" $?
"$MB" rm "$j" >/dev/null

# ---- 6. monitor killed: dead, leftovers, rm -f ----
d=$(run_d --pids 32 --net veth -- sleep 300)
mpid=$(field "$d" monitor_pid)
ipid=$(field "$d" pid)
kill -9 "$mpid"
ok=1
for i in $(seq 1 50); do
    if "$MB" ps | grep "^$d" | grep -q dead; then ok=0; break; fi
    sleep 0.1
done
report "ps shows the container as dead after kill -9 of its monitor" $ok
[ -d "$CG_BASE/box-$d" ] && [ "$(veth_count)" -eq 1 ] && kill -0 "$ipid" 2>/dev/null
report "the dead container left its init, cgroup and veth behind" $?
"$MB" rm -f "$d" >/dev/null
[ ! -d "$CG_BASE/box-$d" ]
report "rm -f removed the cgroup" $?
wait_no_veth
report "rm -f removed the veth interface" $? "$(ip -o link show | grep vethh)"
! kill -0 "$ipid" 2>/dev/null
report "rm -f killed the surviving init" $?
[ ! -d "$STATE/$d" ]
report "rm -f removed the state directory" $?

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
