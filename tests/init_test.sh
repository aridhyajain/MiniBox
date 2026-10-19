#!/usr/bin/env bash
# init_test.sh - checks `minibox run --init`: a tiny PID 1 that reaps zombies and forwards signals.
# Usage: sudo bash tests/init_test.sh     (run from anywhere; takes about 40 seconds)
# Uses a temporary state directory (MINIBOX_STATE_DIR) that is removed at the end.
# Every minibox call goes through `timeout`, so a hang becomes a FAIL instead of a stuck test.
# INFO lines are control measurements (the same thing WITHOUT --init); they never fail the script.
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0

# shellcheck source=lib.sh
. "$ROOT_DIR/tests/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo bash tests/init_test.sh"
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
for tool in ip timeout awk; do
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
export MINIBOX_STATE_DIR="$STATE"

# Remove whatever is still there (even after a failed check or Ctrl-C), then the temp dir.
cleanup() {
    local id
    for id in $(timeout 20 "$MB" ps -a 2>/dev/null | awk 'NR>1 {print $1}'); do
        timeout 20 "$MB" rm -f "$id" >/dev/null 2>&1
    done
    rm -rf "$STATE"
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

# mb args...: minibox with a 30 s ceiling and stdin closed (no tty, so no interactive surprises).
mb() {
    timeout 30 "$MB" "$@" </dev/null
}

# run_fg [options] -- command...: foreground container; its exit status is minibox's.
run_fg() {
    timeout 30 "$MB" run --rootfs "$ROOTFS" "$@" </dev/null
}

# run_d [options] -- command...: detached container; prints the ID.
run_d() {
    timeout 30 "$MB" run -d --rootfs "$ROOTFS" "$@" </dev/null
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

# exec_in <id|name> command...: run a command in the container, stdin closed.
exec_in() {
    local target=$1
    shift
    mb exec "$target" -- "$@"
}

# elapsed <start>: seconds since <start> (a `date +%s.%N` value), two decimals.
elapsed() {
    awk -v s="$1" -v e="$(date +%s.%N)" 'BEGIN { printf "%.2f", e - s }'
}

# below <value> <limit>: success if value < limit (floating point).
below() {
    awk -v v="$1" -v l="$2" 'BEGIN { exit !(v < l) }'
}

# ---- a. PID 1 is the init, the command is its child ----
a=$(run_d --init --memory 64M -- sleep 300)
echo "$a" | grep -Eq '^[0-9a-f]{12}$'
report "run -d --init prints a 12-hex ID" $? "got '$a'"
sleep 0.5
out=$(exec_in "$a" cat /proc/1/comm)
[ "$out" = minibox-init ]
report "with --init, PID 1 is minibox-init (/proc/1/comm)" $? "got '$out'"
out=$(exec_in "$a" cat /proc/2/comm)
[ "$out" = sleep ]
report "with --init, the command (sleep) is PID 2" $? "got '$out'"
out=$(exec_in "$a" sh -c "sed 's/^.*) //' /proc/2/stat | cut -d' ' -f2")
[ "$out" = 1 ]
report "with --init, the command's parent is PID 1" $? "parent '$out'"

ctl=$(run_d --memory 64M -- sleep 300)
sleep 0.5
out=$(exec_in "$ctl" cat /proc/1/comm)
[ "$out" = sleep ]
report "control: without --init the command itself is PID 1" $? "got '$out'"

# ps shows the marker, ps --json has a real boolean
mb ps --json | tr -d ' \n' | grep -q "\"id\":\"$a\".*\"init\":true"
report "ps --json: init is true for the --init container" $?
mb ps --json | tr -d ' \n' | grep -q "\"id\":\"$ctl\".*\"init\":false"
report "ps --json: init is false for the control container" $?
mb ps | awk -v id="$a" '$1 == id && $5 == "yes" { f = 1 } END { exit !f }'
report "ps table: INIT column says yes for the --init container" $?
grep -q '^init=1$' "$STATE/$a/state" && grep -q '^init=0$' "$STATE/$ctl/state"
report "the state file records init=1 / init=0" $?

# ---- g. exec into an --init container ----
out=$(exec_in "$a" echo hello)
[ "$out" = hello ]
report "exec into a --init container prints its output" $? "got '$out'"
exec_in "$a" sh -c 'exit 3'
code=$?
[ "$code" -eq 3 ]
report "exec into a --init container propagates the exit code (3)" $? "got $code"
out=$(exec_in "$a" ps)
echo "$out" | grep -Eq '^ *[0-9]+ .* ps *$' && echo "$out" | grep -q 'sleep'
report "exec -- ps inside a --init container lists sleep and itself" $? "ps output: $(echo "$out" | tr '\n' '|')"

# An exec'd command that is killed leaves an orphan; the init reaps it (no zombie, unlike the control).
base=$(live_count "$a")
timeout 30 "$MB" exec "$a" -- sleep 30 </dev/null >/dev/null 2>&1 &
bg=$!
sleep 1
kill -KILL "$bg"
wait_live "$a" "$base" 2
report_live "exec -- sleep 30 killed with SIGKILL: its orphan is gone within 2 s" $? "$a" "$base"
wait "$bg" 2>/dev/null
sleep 0.3
z=$(zombie_count "$a")
[ "$z" -eq 0 ]
report "... and left no zombie behind (init reaped it)" $? "zombies: $z"
echo "INFO: control container (no --init) after the same kill would show a zombie sometimes; see phase4b_test.sh"

# ---- d. signal forwarding: stop is quick with --init, uses the grace period without ----
start=$(date +%s.%N)
mb stop "$a" >/dev/null
t=$(elapsed "$start")
code=$(field "$a" exit_code)
below "$t" 2
report "stop of a --init container finishes in under 2 s" $? "took ${t}s"
[ "$code" -eq 143 ]
report "... with exit code 143 (128+SIGTERM), not 137" $? "exit_code $code"

start=$(date +%s.%N)
mb stop --time 1 "$ctl" >/dev/null
t=$(elapsed "$start")
echo "INFO: control: stop without --init took ${t}s and ended with exit code $(field "$ctl" exit_code)" \
     "(expected about 1 s grace + SIGKILL = 137)"

# a second signal that is forwarded: SIGUSR1 reaches the command (sh trap prints, then exits)
u=$(run_d --init -- sh -c 'trap "echo got-usr1; exit 0" USR1; while :; do sleep 1; done')
sleep 0.7
kill -USR1 "$(field "$u" pid)"
wait_status "$u" exited 5
[ "$(field "$u" exit_code)" -eq 0 ] && mb logs "$u" | grep -q got-usr1
report "SIGUSR1 sent to the init is forwarded to the command (trap ran, exit 0)" $? \
       "exit_code $(field "$u" exit_code), log: $(mb logs "$u" | tr '\n' '|')"

# ---- b. orphan reaping ----
run_orphan() { # run_orphan [--init]: detached container whose command leaves an orphan; echoes the ID
    run_d "$@" --memory 64M -- sh -c '(sleep 0.3 &); sleep 3'
}
r=$(run_orphan --init)
sleep 1.5
z=$(zombie_count "$r")
[ "$z" -eq 0 ]
report "with --init the orphan is reaped: 0 zombies after 1.5 s" $? "zombies $z (pids.current minus live)"
wait_status "$r" exited 8

c=$(run_orphan)
sleep 1.5
echo "INFO: control: without --init $(zombie_count "$c") zombie(s) after 1.5 s (expected 1: PID 1 never wait()s)"
wait_status "$c" exited 8

# ---- c. exit code propagation ----
run_fg --init -- sh -c 'exit 7'
code=$?
[ "$code" -eq 7 ]
report "--init: exit 7 is returned by minibox run" $? "got $code"
run_fg --init -- sh -c 'kill -TERM $$'
code=$?
[ "$code" -eq 143 ]
report "--init: command killed by SIGTERM gives 143" $? "got $code"
run_fg --init -- sh -c 'kill -KILL $$'
code=$?
[ "$code" -eq 137 ]
report "--init: command killed by SIGKILL gives 137" $? "got $code"
out=$(run_fg --init -- /no/such/program 2>&1)
code=$?
[ "$code" -eq 127 ] && echo "$out" | grep -q "no/such/program"
report "--init: a program that does not exist gives 127 and a message" $? "code $code, output: $out"
out=$(run_fg --init -- /etc/passwd 2>&1)
code=$?
[ "$code" -eq 126 ]
report "--init: a file that cannot be executed gives 126" $? "code $code, output: $out"
out=$(run_fg --init -- sh -c 'echo hi; exit 0')
[ "$out" = hi ]
report "--init: output of the command reaches stdout" $? "got '$out'"

# ---- e. a program that is PID 1 cannot kill itself; under --init it is not PID 1 ----
start=$(date +%s.%N)
timeout 10 "$MB" run --rootfs "$ROOTFS" --init -- timeout 2 sh -c 'while :; do :; done' </dev/null >/dev/null 2>&1
t=$(elapsed "$start")
below "$t" 6
report "--init -- timeout 2 <busy loop> ends by itself within 6 s (the earlier benchmark hang)" $? "took ${t}s"

# ---- f. together with limits and the network ----
out=$(run_fg --init --memory 64M --cpus 0.5 --pids 20 --stats -- sh -c 'echo limits-ok' 2>&1)
code=$?
echo "$out" | grep -q limits-ok && echo "$out" | grep -q 'pids' && [ "$code" -eq 0 ]
report "--init with --memory --cpus --pids --stats runs and prints stats" $? "code $code, output: $out"
out=$(run_fg --init --pids 2 -- sh -c 'echo two-ok' 2>&1)
[ "$out" = two-ok ]
report "--init --pids 2 (init + command) works" $? "got '$out'"
out=$(run_fg --init --pids 1 -- true 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "at least 2"
report "--init --pids 1 is refused with a clear message" $? "code $code, output: $out"

n=$(run_d --init --net veth --memory 64M -- sleep 300)
sleep 0.5
ip_n=$(field "$n" ip)
out=$(exec_in "$n" ip addr show eth0)
echo "$ip_n" | grep -Eq '^10\.0\.[0-9]+\.2$' && echo "$out" | grep -q "inet $ip_n/"
report "--init --net veth: eth0 has the address $ip_n" $? "got: $(echo "$out" | tr '\n' '|')"
if command -v ping >/dev/null 2>&1; then
    host_ip="${ip_n%.2}.1"
    out=$(exec_in "$n" ping -c 1 -W 2 "$host_ip" 2>&1)
    [ $? -eq 0 ]
    report "--init --net veth: the container can ping the host end $host_ip" $? "got: $(echo "$out" | tr '\n' '|')"
fi
mb stop "$n" >/dev/null
[ "$(field "$n" exit_code)" -eq 143 ]
report "--init --net veth container stops with 143" $? "exit_code $(field "$n" exit_code)"

# ---- cleanup checks ----
for id in $(mb ps -a | awk 'NR>1 {print $1}'); do
    mb rm -f "$id" >/dev/null
done
left=$(find "$STATE" -mindepth 1 -maxdepth 1 -type d | wc -l)
[ "$left" -eq 0 ]
report "no container state is left" $? "$left directories in $STATE"
ls -d "$CG_BASE"/box-* >/dev/null 2>&1
[ $? -ne 0 ]
report "no box-* cgroup is left" $? "$(ls -d "$CG_BASE"/box-* 2>/dev/null)"
wait_no_veth
report "no vethh* interface is left" $? "$(ip -o link show | grep vethh)"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
