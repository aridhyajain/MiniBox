#!/usr/bin/env bash
# phase2_test.sh - checks IPC + network namespace isolation, loopback, veth.
# Usage: sudo tests/phase2_test.sh      (run from anywhere; takes ~15 s)
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
FAILS=0
HOST_QID=""

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo tests/phase2_test.sh"
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
for tool in ip ping ipcmk ipcs ipcrm; do
    command -v "$tool" >/dev/null 2>&1 || { echo "host tool '$tool' missing"; exit 1; }
done

# Always remove the host IPC object, even if the script is interrupted.
cleanup() {
    [ -n "$HOST_QID" ] && ipcrm -q "$HOST_QID" 2>/dev/null
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

# box <command...>: default container (loopback only), stdin closed.
box() {
    "$MB" run --rootfs "$ROOTFS" -- "$@" </dev/null
}

# vbox <command...>: container with --net veth.
vbox() {
    "$MB" run --rootfs "$ROOTFS" --net veth -- "$@" </dev/null
}

veth_count() {
    ip -o link show | grep -c 'vethh'
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

# ---- 1. only lo, and it is UP ----
links=$(box ip link)
names=$(echo "$links" | grep -E '^[0-9]+:' | awk '{print $2}' | tr -d ':' | tr '\n' ' ')
[ "$names" = "lo " ]
report "ip link inside shows only lo" $? "got: $names"

echo "$links" | grep -Eq '^[0-9]+: lo:.*<[^>]*UP'
report "lo is UP" $?

# ---- 2. loopback works ----
box ping -c 1 -W 2 127.0.0.1 >/dev/null 2>&1
report "ping 127.0.0.1 inside works" $?

# ---- 3. IPC namespace: host queue invisible inside ----
HOST_QID=$(ipcmk -Q | awk '{print $NF}')
if echo "$HOST_QID" | grep -Eq '^[0-9]+$' &&
   ipcs -q | awk '{print $2}' | grep -qx "$HOST_QID"; then
    inside_q=$(box ipcs -q)
    echo "$inside_q" | awk '{print $2}' | grep -qx "$HOST_QID"
    [ $? -ne 0 ]
    report "host IPC queue $HOST_QID not visible inside" $? "inside ipcs -q:
$inside_q"
else
    report "create host IPC queue with ipcmk" 1 "got '$HOST_QID'"
fi
cleanup
HOST_QID=""

# ---- 4. veth: container -> host, and eth0 has 10.0.0.2 ----
vbox ping -c 1 -W 2 10.0.0.1 >/dev/null 2>&1
report "--net veth: container can ping 10.0.0.1" $?

vbox ip addr show eth0 | grep -q 'inet 10.0.0.2/24'
report "--net veth: container eth0 has 10.0.0.2/24" $?

# ---- 4b. veth: host -> container (needs the container to stay alive) ----
"$MB" run --rootfs "$ROOTFS" --net veth -- sleep 8 </dev/null >/dev/null 2>&1 &
bg=$!
ok=1
for i in $(seq 1 25); do
    kill -0 "$bg" 2>/dev/null || break
    if ping -c 1 -W 1 10.0.0.2 >/dev/null 2>&1; then ok=0; break; fi
    sleep 0.2
done
report "--net veth: host can ping 10.0.0.2" $ok
wait "$bg"

# ---- 5. no veth left after exit ----
wait_no_veth
report "no vethh* interface left on host after exit" $? "$(ip -o link show | grep vethh)"

# ---- 6. failure path: command not found ----
vbox nosuchcmd >/dev/null 2>&1
code=$?
[ "$code" -eq 127 ]
report "failing command exits 127 with --net veth" $? "got $code"
wait_no_veth
report "no vethh* interface left after failed container" $?

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
