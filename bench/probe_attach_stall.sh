#!/usr/bin/env bash
# probe_attach_stall.sh - when is a container start with limits slow? clone3 vs cgroup migration.
#
# Usage: sudo bench/probe_attach_stall.sh [output-file]
#   Needs root (minibox does), the built ./minibox and rootfs/ in the repository root.
#   With an output file the table is also written there; an existing file is never overwritten
#   (bench/results/analysis/attach_stall_probe.txt is a real result and must stay as it is).
#
# What it measures: the wall-clock time in milliseconds of `minibox run ... -- /bin/true`, once
# with the default path (clone3 + CLONE_INTO_CGROUP: the process is born inside its cgroup) and
# once with MINIBOX_NO_CLONE3=1 (clone(), then the parent writes the PID to cgroup.procs: a
# cgroup migration of a running process). Two parts:
#   1. Seven limit combinations, each with `--net none`, 12 timed runs after 2 warm-ups, one
#      after the other. Question: do the limits themselves make the migration path slow?
#   2. A limited run (--net none --memory 64M --cpus 0.5 --pids 20) started right after a
#      `--net veth` run (10 pairs). Question: does a network-device teardown just before the
#      run make the migration path slow?
#
# How to read the output: one line per row: the options, the mode (clone3 or migrate), then the
# times in ms sorted ascending (so the first value is the minimum and the last the maximum).
# A row with all values close together has no stall. Compare the two modes of the same row.
# The mechanism (an RCU grace period started by the network teardown) is a hypothesis; this
# script only shows when the slow runs happen, not why.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${1:-}

die() { echo "error: $1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (minibox needs it): sudo bench/probe_attach_stall.sh [output-file]"
[ -x "$ROOT_DIR/minibox" ] || die "minibox not built; run 'make' first"
[ -x "$ROOT_DIR/rootfs/bin/busybox" ] || die "Alpine rootfs missing; run 'make rootfs' first"
if [ -n "$OUT" ] && [ -e "$OUT" ]; then
    die "$OUT already exists; give a new file name (results are never overwritten)"
fi
cd "$ROOT_DIR" || exit 1

now() { date +%s%N; }

# run_ms ENV_ASSIGNMENT MINIBOX_OPTIONS...: one `minibox run ... -- /bin/true`, prints whole ms.
run_ms() {
    local envs="$1"; shift
    local s e
    s=$(now)
    env $envs ./minibox run --rootfs rootfs "$@" -- /bin/true >/dev/null 2>&1
    e=$(now)
    echo $(( (e - s) / 1000000 ))
}

# sorted_line: reads "v1 v2 ..." as arguments, prints them sorted ascending on one line.
sorted_line() { echo "$@" | tr ' ' '\n' | sort -n | tr '\n' ' '; }

# env_for MODE: the environment assignment for "clone3" (none) or "migrate" (force the old path).
env_for() { [ "$1" = migrate ] && echo "MINIBOX_NO_CLONE3=1"; }

# Part 1: the limit combinations, one after the other.
part1() {
    local opts mode envs vals i
    echo "ms per run, sorted, 12 runs after 2 warm-ups"
    for opts in "--net none" "--net none --memory 64M" "--net none --pids 20" "--net none --cpus 0.5" \
                "--net none --cpus 1" "--net none --memory 256M --cpus 1 --pids 64" \
                "--net none --memory 64M --cpus 0.5 --pids 20"; do
        for mode in clone3 migrate; do
            envs=$(env_for "$mode")
            run_ms "$envs" $opts >/dev/null; run_ms "$envs" $opts >/dev/null
            vals=""
            for i in $(seq 1 12); do vals="$vals $(run_ms "$envs" $opts)"; done
            printf '%-46s %-8s %s\n' "$opts" "$mode" "$(sorted_line $vals)"
        done
    done
}

# Part 2: a limited run right after a veth run (10 pairs per mode).
part2() {
    local mode envs vals i
    for mode in clone3 migrate; do
        envs=$(env_for "$mode")
        vals=""
        for i in $(seq 1 10); do
            ./minibox run --rootfs rootfs --net veth -- /bin/true >/dev/null 2>&1
            vals="$vals $(run_ms "$envs" --net none --memory 64M --cpus 0.5 --pids 20)"
        done
        printf '%-46s %-8s %s\n' "limited run right after a veth run" "$mode" "$(sorted_line $vals)"
    done
}

if [ -n "$OUT" ]; then
    { part1; part2; } | tee "$OUT"
else
    part1; part2
fi
