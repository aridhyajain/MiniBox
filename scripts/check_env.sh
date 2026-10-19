#!/usr/bin/env bash
# check_env.sh - verify the machine can build and run minibox.
# Usage: scripts/check_env.sh      (do NOT run with sudo)
# Exit status: 0 if every required check passed, 1 otherwise.

FAILS=0
FIXES=()

pass() { printf '  [PASS] %s\n' "$1"; }
info() { printf '  [INFO] %s\n' "$1"; }
fail() { # fail <message> <suggested fix>
    printf '  [FAIL] %s\n' "$1"
    FAILS=$((FAILS + 1))
    FIXES+=("$2")
}

# Kernel version: namespaces and cgroup v2 need a reasonably modern kernel.
check_kernel() {
    echo "Kernel"
    local kver
    kver=$(uname -r)
    info "kernel release: $kver"
    local major minor
    major=${kver%%.*}
    minor=${kver#*.}; minor=${minor%%.*}
    if [ "$major" -gt 4 ] || { [ "$major" -eq 4 ] && [ "$minor" -ge 15 ]; }; then
        pass "kernel >= 4.15"
    else
        fail "kernel $kver is too old" "Update the kernel (on WSL2: 'wsl --update' from Windows PowerShell)."
    fi
}

# WSL2 kernels identify themselves as '...microsoft-standard-WSL2'.
check_wsl() {
    echo "Platform"
    if grep -qi microsoft /proc/version 2>/dev/null; then
        if uname -r | grep -qi 'wsl2'; then
            pass "running on WSL2"
        else
            fail "running on WSL1 (no real Linux kernel)" "Convert the distro: 'wsl --set-version <distro> 2' from PowerShell."
        fi
    else
        info "not WSL (native Linux) - fine for minibox"
    fi
}

# cgroup v2 = single unified hierarchy; filesystem type shows as cgroup2fs.
check_cgroup() {
    echo "cgroup v2"
    local fstype
    fstype=$(stat -fc %T /sys/fs/cgroup 2>/dev/null)
    if [ "$fstype" = "cgroup2fs" ]; then
        pass "cgroup v2 mounted at /sys/fs/cgroup"
    else
        fail "cgroup v2 not mounted at /sys/fs/cgroup (type: ${fstype:-none})" \
             "Enable it: add 'kernelCommandLine = cgroup_no_v1=all' under [wsl2] in %UserProfile%\\.wslconfig, then 'wsl --shutdown'."
        return
    fi
    local ctrls
    ctrls=$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null)
    info "available controllers: ${ctrls:-none}"
    local c
    for c in memory cpu pids; do
        case " $ctrls " in
            *" $c "*) pass "controller '$c' available" ;;
            *) fail "controller '$c' not available at root" \
                    "Ensure the '$c' controller is enabled (check systemd/.wslconfig; cgroup v2 only)." ;;
        esac
    done
}

# Unprivileged user namespaces: sysctl knobs differ by distro, so also try it.
check_userns() {
    echo "User namespaces"
    local max clone
    max=$(cat /proc/sys/user/max_user_namespaces 2>/dev/null)
    clone=$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null)
    info "user.max_user_namespaces=${max:-n/a} kernel.unprivileged_userns_clone=${clone:-n/a}"
    if command -v unshare >/dev/null 2>&1 && unshare -U true 2>/dev/null; then
        pass "unprivileged user namespaces work"
    else
        fail "unprivileged user namespaces disabled" \
             "Run: sudo sysctl -w kernel.unprivileged_userns_clone=1 user.max_user_namespaces=15000"
    fi
}

check_tools() {
    echo "Tools"
    local t
    for t in gcc make python3; do
        if command -v "$t" >/dev/null 2>&1; then
            pass "$t found ($(command -v "$t"))"
        else
            fail "$t missing" "Run: sudo apt update && sudo apt install -y build-essential python3"
        fi
    done
    if command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1; then
        pass "wget or curl found"
    else
        fail "neither wget nor curl found" "Run: sudo apt install -y wget"
    fi
}

echo "=== minibox environment check ==="
if [ "$(id -u)" -eq 0 ]; then
    echo "Note: running as root; user-namespace results may be misleading."
fi
check_kernel
check_wsl
check_cgroup
check_userns
check_tools

echo
echo "=== Summary ==="
if [ "$FAILS" -eq 0 ]; then
    echo "PASS: environment is ready."
    exit 0
fi
echo "FAIL: $FAILS check(s) failed. Suggested fixes:"
for f in "${FIXES[@]}"; do
    echo "  - $f"
done
exit 1
