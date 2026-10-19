#!/usr/bin/env bash
# demo_review1.sh - interactive minibox demo for Review 1 (about 5 minutes).
# Usage: sudo scripts/demo_review1.sh [--auto]
#   --auto  wait 2 seconds instead of Enter before each step (for recording).

AUTO=0
[ "${1:-}" = "--auto" ] && AUTO=1

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR" || exit 1

if [ "$(id -u)" -ne 0 ]; then
    echo "This demo must run as root: sudo scripts/demo_review1.sh [--auto]"
    exit 1
fi
if [ ! -x ./minibox ]; then
    echo "./minibox not found. Build it first: make"
    exit 1
fi
if [ ! -x rootfs/bin/busybox ]; then
    echo "rootfs/ is missing or incomplete. Run: make rootfs   (then the sudo tar command it prints)"
    exit 1
fi
for tool in ipcmk ipcs ipcrm ip; do
    command -v "$tool" >/dev/null 2>&1 || { echo "host tool '$tool' missing"; exit 1; }
done

if [ -t 1 ]; then B=$'\033[1m'; C=$'\033[36m'; Y=$'\033[33m'; N=$'\033[0m'; else B=; C=; Y=; N=; fi
STEP=0

# Remove the host IPC queue if the demo is interrupted between create and remove.
trap '[ -n "${ID:-}" ] && ipcrm -q "$ID" 2>/dev/null' EXIT

# step <heading> <what it proves> <command>...
# Prints heading, sentence and ALL commands, waits, then runs the commands.
step() {
    local heading=$1 why=$2 c
    shift 2
    STEP=$((STEP + 1))
    echo
    echo "${B}=== Step $STEP: $heading ===${N}"
    echo "$why"
    for c in "$@"; do
        echo "${C}\$ ${c}${N}"
    done
    if [ "$AUTO" -eq 1 ]; then
        sleep 2
    else
        read -r -p "${Y}[Enter to run]${N} " _
    fi
    for c in "$@"; do
        eval "$c" </dev/null
        local rc=$?
        [ "$rc" -ne 0 ] && echo "${Y}[exit status $rc]${N}"
    done
    return 0
}

# Inner scripts with nested quotes are kept literal in here-documents.
read -r -d '' CMD_HOST_NS <<'EOF'
echo "host: $(hostname)"; for n in pid uts mnt; do echo "$n: $(readlink /proc/self/ns/$n)"; done
EOF

read -r -d '' CMD_BOX_NS <<'EOF'
./minibox run --rootfs rootfs --hostname demobox -- sh -c 'echo "container: $(hostname)"; for n in pid uts mnt; do echo "$n: $(readlink /proc/self/ns/$n)"; done'
EOF

read -r -d '' CMD_FORK <<'EOF'
./minibox run --rootfs rootfs --pids 20 --stats -- sh -c 'sh -c "for i in \$(seq 1 100); do sleep 30 & done" 2>/dev/null; set -- /proc/[0-9]*; echo "processes alive in container: $#"'
EOF

read -r -d '' CMD_HOG <<'EOF'
./minibox run --rootfs rootfs --memory 32M --stats -- sh -c 'x=aaaaaaaa; while :; do x=$x$x; done'
EOF

read -r -d '' CMD_CPU <<'EOF'
./minibox run --rootfs rootfs --cpus 0.5 --stats -- sh -c 'exec 2>/dev/null; timeout 3 sh -c "while :; do :; done"; true'
EOF

echo "${B}minibox - Review 1 demo${N}  (namespaces + pivot_root + cgroups v2, written in C)"
[ "$AUTO" -eq 1 ] && echo "(auto mode: 2 s pause per step)"

step "Host vs container: namespace IDs" \
     "Same commands on the host and in a container: the namespace IDs (inode numbers) and hostname differ, so the container has its own PID, UTS and mount namespaces." \
     "$CMD_HOST_NS" "$CMD_BOX_NS"

step "Inside the container" \
     "The container is PID 1, sees only its own processes, and its / is the Alpine rootfs; the host's /home is not visible." \
     "echo \"host /home: \$(ls /home | tr '\\n' ' ')\"" \
     "./minibox run --rootfs rootfs --hostname mybox -- sh -c 'hostname; echo \"PID: \$\$\"; ps; ls /; echo \"container /home: [\$(ls /home)]\"'"

step "Network namespace" \
     "A default container has only a loopback interface; with --net veth it gets a virtual cable to the host and can ping it." \
     'echo "-- container with default network --"; ./minibox run --rootfs rootfs -- ip link' \
     'echo "-- container with --net veth --"; ./minibox run --rootfs rootfs --net veth -- sh -c '"'"'ip addr show eth0; ping -c 2 -W 2 10.0.0.1'"'"

step "IPC namespace" \
     "A message queue created on the host is invisible inside the container, because the container has its own IPC namespace." \
     'ID=$(ipcmk -Q | awk "{print \$NF}"); echo "host queue id: $ID"; ipcs -q' \
     "./minibox run --rootfs rootfs -- ipcs -q" \
     'ipcrm -q "$ID" && unset ID'

step "Memory limit (cgroup memory.max = 32M)" \
     "A process that keeps doubling a string is killed by the kernel's OOM killer inside its cgroup; minibox reports it and exits with 137." \
     "$CMD_HOG"

step "Process limit (cgroup pids.max = 20)" \
     "A fork bomb is contained: fork() fails once the cgroup holds 20 processes, so the host is unaffected." \
     "$CMD_FORK"

step "CPU limit (cgroup cpu.max = 50000 100000)" \
     "A busy loop for 3 seconds gets about half a core, so cpu.usage_usec is near 1.5 s." \
     "$CMD_CPU"

sleep 1   # the kernel removes the veth a moment after the container exits
step "Cleanup proof" \
     "After all runs minibox left nothing behind: no box-* cgroups and no vethh* interfaces on the host." \
     'echo "cgroups left: $(ls -d /sys/fs/cgroup/minibox/box-* 2>/dev/null | wc -l)"' \
     'echo "veth interfaces left: $(ip -o link show | grep -c vethh)"'

echo
echo "${B}Demo finished.${N}"
