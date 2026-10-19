#!/usr/bin/env bash
# lib.sh - helpers shared by tests/phase4b_test.sh and tests/phase5_test.sh.
# Not run on its own: the test scripts `source` it. They must define report() and CG_BASE
# (default /sys/fs/cgroup/minibox) before they call anything from here.
#
# Why these tests count cgroup.procs and NOT pids.current
# -------------------------------------------------------
# When a command started by `minibox exec` is killed, its children are orphans and the kernel
# reparents them to the PID 1 of the container's PID namespace. minibox's PID 1 is the user's
# command (e.g. `sleep 300`) and never calls wait(), so a dead orphan stays a zombie (state Z):
# it no longer runs, but its process-table entry exists until its parent reaps it.
# pids.current counts every task that still exists, zombies included, so it stays above the
# baseline. cgroup.procs lists only processes that are alive, so counting its lines answers the
# question the tests really ask: "is anything still RUNNING in the container?".

# live_count <id>: number of live processes in the container's cgroup (cgroup.procs lines).
live_count() {
    local n
    n=$(wc -l < "${CG_BASE:-/sys/fs/cgroup/minibox}/box-$1/cgroup.procs" 2>/dev/null) || n=999
    echo "${n:-999}"
}

# wait_live <id> <max> <seconds>: poll until the cgroup holds at most <max> live processes.
wait_live() {
    local i
    for i in $(seq 1 $(($3 * 10))); do
        [ "$(live_count "$1")" -le "$2" ] && return 0
        sleep 0.1
    done
    return 1
}

# proc_field <pid> <n>: field n of /proc/<pid>/stat counted after the "(comm)" part, so a command
# name with spaces or parentheses cannot shift the columns. n=1 is the state, n=2 the parent PID.
proc_field() {
    sed 's/^.*) //' "/proc/$1/stat" 2>/dev/null | cut -d' ' -f"$2"
}

# zombie_count <id>: unreaped zombies = pids.current (counts zombies) - live processes.
zombie_count() {
    local cur
    cur=$(cat "${CG_BASE:-/sys/fs/cgroup/minibox}/box-$1/pids.current" 2>/dev/null || echo 0)
    echo $((cur - $(live_count "$1")))
}

# note_zombies <id>: informational only, never a failure. Zombies appear because the container's
# PID 1 never reaps the orphans that were reparented to it (see the comment at the top).
note_zombies() {
    echo "INFO: $(zombie_count "$1") unreaped zombie(s) in box-$1 (pids.current minus live processes; PID 1 never wait()s)"
}

# dump_cgroup <id>: every process still listed in the cgroup: host PID, state, command line.
dump_cgroup() {
    local pid
    echo "      processes in box-$1 (host PID, state, command):"
    for pid in $(cat "${CG_BASE:-/sys/fs/cgroup/minibox}/box-$1/cgroup.procs" 2>/dev/null); do
        printf '        %s  %s  %s\n' "$pid" "$(proc_field "$pid" 1)" \
               "$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
    done
    echo "      $(note_zombies "$1")"
}

# report_live <name> <rc> <id> <baseline>: like report(), but a failure also explains itself by
# listing the live processes and comparing the live count with the baseline.
report_live() {
    report "$1" "$2" "live processes $(live_count "$3"), baseline $4"
    [ "$2" -eq 0 ] || dump_cgroup "$3"
}

# zombies_of <host pid>: number of zombie processes whose parent is that host PID
# (found by scanning /proc/*/stat for state Z and the given parent PID).
zombies_of() {
    local s pid n=0
    for s in /proc/[0-9]*/stat; do
        pid=${s#/proc/}
        pid=${pid%/stat}
        [ "$(proc_field "$pid" 1)" = Z ] && [ "$(proc_field "$pid" 2)" = "$1" ] && n=$((n + 1))
    done
    echo "$n"
}

# state_leftovers <state dir>: prints everything in the state directory except the .lock file that
# minibox creates and keeps on purpose (container subdirectories, stray files). Empty output = clean.
state_leftovers() {
    find "$1" -mindepth 1 -maxdepth 1 ! -name .lock -printf '%f\n' 2>/dev/null | tr '\n' ' '
}

# state_clean <state dir>: succeeds when state_leftovers prints nothing; on failure prints what it found.
state_clean() {
    local found
    found=$(state_leftovers "$1")
    [ -z "$found" ] && return 0
    echo "$found"
    return 1
}
