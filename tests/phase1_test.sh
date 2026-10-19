#!/usr/bin/env bash
# phase1_test.sh - checks PID/UTS/mount isolation and pivot_root.
# Usage: sudo tests/phase1_test.sh      (run from anywhere)
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
BOX_HOSTNAME="mybox"
FAILS=0

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo tests/phase1_test.sh"
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

report() { # report <name> <0 if pass> [detail]
    if [ "$2" -eq 0 ]; then
        echo "PASS: $1"
    else
        echo "FAIL: $1 ${3:+($3)}"
        FAILS=$((FAILS + 1))
    fi
}

# box <command...>: run a command in a fresh container, stdin closed.
box() {
    "$MB" run --rootfs "$ROOTFS" --hostname "$BOX_HOSTNAME" -- "$@" </dev/null
}

# 1. hostname differs from the host's (UTS namespace)
host_name=$(hostname)
inside_name=$(box hostname)
[ "$inside_name" = "$BOX_HOSTNAME" ] && [ "$inside_name" != "$host_name" ]
report "hostname inside ('$inside_name') differs from host ('$host_name')" $? "expected $BOX_HOSTNAME"

# 2. the container's first process is PID 1 (PID namespace)
pid=$(box sh -c 'echo $$')
[ "$pid" = "1" ]
report "echo \$\$ prints 1" $? "got '$pid'"

# 3. ps shows only container processes
ps_out=$(box sh -c 'ps')
pids=$(echo "$ps_out" | awk 'NR>1 {print $1}')
count=$(echo "$pids" | grep -c .)
max=$(echo "$pids" | sort -n | tail -n 1)
[ "$count" -ge 1 ] && [ "$count" -le 5 ] && [ "${max:-999}" -le 10 ]
report "ps shows only container processes" $? "count=$count max_pid=$max"

# 4. /proc is mounted (and is a fresh procfs)
mounts=$(box cat /proc/mounts)
echo "$mounts" | grep -q '^proc /proc proc'
report "/proc is mounted" $?

# 5. the host's /home is not visible (pivot_root worked)
host_homes=$(ls -A /home)
inside_homes=$(box ls -A /home)
leak=0
for h in $host_homes; do
    echo "$inside_homes" | grep -qx "$h" && leak=1
done
os_name=$(box cat /etc/os-release | grep '^ID=')
[ "$leak" -eq 0 ] && [ "$os_name" = "ID=alpine" ]
report "host /home not visible, root is Alpine" $? "leak=$leak os='$os_name'"

# 6. exit code of the command is propagated
box sh -c 'exit 42'
code=$?
[ "$code" -eq 42 ]
report "container exits with the command's exit code" $? "got $code"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
exit 1
