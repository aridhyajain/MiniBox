#!/usr/bin/env bash
# phase4b_test.sh - checks `minibox exec`: running commands inside a running container.
# Usage: sudo tests/phase4b_test.sh     (run from anywhere; takes about 30 seconds)
# Uses a temporary state directory (MINIBOX_STATE_DIR) that is removed at the end.
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo tests/phase4b_test.sh"
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
for tool in ip hostname; do
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
    for id in $("$MB" ps -a 2>/dev/null | awk 'NR>1 {print $1}'); do
        "$MB" rm -f "$id" >/dev/null 2>&1
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

# exec_in <id|name> command...: run a command in the container, stdin closed.
exec_in() {
    local target=$1
    shift
    "$MB" exec "$target" -- "$@" </dev/null
}

# ---- 1. start the containers ----
a=$(run_d --name worker --hostname boxy --memory 64M --pids 20 -- sleep 300)
echo "$a" | grep -Eq '^[0-9a-f]{12}$'
report "run -d with limits prints a 12-hex ID" $? "got '$a'"
b=$(run_d --net veth -- sleep 300)
echo "$b" | grep -Eq '^[0-9a-f]{12}$'
report "run -d --net veth prints a 12-hex ID" $? "got '$b'"
[ -d "$CG_BASE/box-$a" ]
report "container A has its cgroup box-<id>" $?

# ---- 2. namespaces: hostname, PIDs ----
host_name=$(hostname)
out=$(exec_in worker hostname)
[ "$out" = boxy ] && [ "$out" != "$host_name" ]
report "exec -- hostname prints boxy (host is '$host_name'); container found by name" $? "got '$out'"

out=$(exec_in "$a" ps)
echo "$out" | grep -Eq '^ *1 .*sleep'
report "exec -- ps lists PID 1 (sleep)" $? "ps output: $(echo "$out" | tr '\n' '|')"
echo "$out" | grep -Eq '^ *[0-9]+ .* ps *$'
report "exec -- ps also lists the exec'd ps itself" $? "ps output: $(echo "$out" | tr '\n' '|')"

out=$(exec_in "$a" sh -c 'echo $$')
echo "$out" | grep -Eq '^[0-9]+$' && [ "$out" -le 100 ]
report "exec -- sh -c 'echo \$\$' prints a small container PID" $? "got '$out'"

# ---- 3. cgroup membership ----
"$MB" exec "$a" -- sleep 5 </dev/null &
bg=$!
sleep 1
current=$(cat "$CG_BASE/box-$a/pids.current" 2>/dev/null)
[ "${current:-0}" -ge 2 ]
report "while 'exec -- sleep 5' runs, the leaf cgroup holds at least 2 processes" $? \
       "pids.current '$current'"
wait "$bg"
sleep 0.3
[ "$(cat "$CG_BASE/box-$a/pids.current" 2>/dev/null)" = 1 ]
report "after it ends only the container's init is left in the cgroup" $? \
       "pids.current '$(cat "$CG_BASE/box-$a/pids.current" 2>/dev/null)'"

# ---- 4. filesystem, stdin, exit codes ----
exec_in "$a" sh -c 'echo hi > /tmp/x'
out=$(exec_in "$a" cat /tmp/x)
[ "$out" = hi ]
report "a file written by one exec is read by a second exec" $? "got '$out'"
[ ! -e "$ROOTFS/tmp/x" ]
report "that file is not in the host's rootfs directory (/tmp is a tmpfs)" $?

out=$(echo piped | "$MB" exec "$a" -- cat)
[ "$out" = piped ]
report "stdin is inherited (echo piped | exec -- cat)" $? "got '$out'"

exec_in "$a" sh -c 'exit 7'
code=$?
[ "$code" -eq 7 ]
report "exit code 7 is propagated" $? "got $code"

"$MB" exec "$a" -- sh -c 'kill -9 $$' </dev/null
code=$?
[ "$code" -eq 137 ]
report "a command killed by SIGKILL gives 137 (128+9)" $? "got $code"

out=$(exec_in "$a" no-such-program 2>&1)
code=$?
[ "$code" -eq 127 ] && echo "$out" | grep -q "no-such-program"
report "a program that does not exist gives 127 and a message" $? "code $code, output: $out"

# ---- 5. network ----
ip_b=$(field "$b" ip)
out=$(exec_in "$b" ip addr show eth0)
echo "$ip_b" | grep -Eq '^10\.0\.[0-9]+\.2$' && echo "$out" | grep -q "inet $ip_b/"
report "exec -- ip addr show eth0 shows the veth address $ip_b" $? "got: $(echo "$out" | tr '\n' '|')"
out=$(exec_in "$a" ip -o link show)
echo "$out" | grep -q 'eth0'
[ $? -ne 0 ]
report "container A (--net none) has no eth0" $? "got: $(echo "$out" | tr '\n' '|')"

# ---- 6. errors ----
out=$("$MB" exec no-such-container -- true 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "no such container"
report "exec on an unknown container fails with a clear message" $? "code $code, output: $out"

out=$("$MB" exec "$a" 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "no command"
report "exec without a command fails with a clear message" $? "code $code, output: $out"
out=$("$MB" exec "$a" -- 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "no command"
report "exec with an empty command after -- fails with a clear message" $? "code $code, output: $out"

e=$(run_d -- sh -c 'exit 3')
wait_status "$e" exited 10
out=$("$MB" exec "$e" -- true 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "not running"
report "exec on an exited container fails with 'not running'" $? "code $code, output: $out"
"$MB" rm "$e" >/dev/null

# Non-root: only if the unprivileged user can execute ./minibox at all (a 750 home may forbid it).
if command -v setpriv >/dev/null 2>&1 &&
   setpriv --reuid=65534 --regid=65534 --clear-groups "$MB" help >/dev/null 2>&1; then
    out=$(setpriv --reuid=65534 --regid=65534 --clear-groups "$MB" exec "$a" -- true 2>&1)
    code=$?
    [ "$code" -ne 0 ] && echo "$out" | grep -q "must be run as root"
    report "exec as a normal user fails with 'must be run as root'" $? "code $code, output: $out"
else
    echo "SKIP: exec as a normal user (setpriv missing or minibox not executable by 'nobody')"
fi

# ---- 7. cleanup ----
"$MB" stop --time 1 "$a" >/dev/null
"$MB" stop --time 1 "$b" >/dev/null
out=$("$MB" exec "$a" -- true 2>&1)
code=$?
[ "$code" -ne 0 ] && echo "$out" | grep -q "not running"
report "exec on the container after 'stop' fails with 'not running'" $? "code $code, output: $out"
"$MB" rm "$a" >/dev/null
"$MB" rm "$b" >/dev/null
[ $? -eq 0 ] && [ ! -d "$STATE/$a" ] && [ ! -d "$STATE/$b" ]
report "rm removed both state directories" $?
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
