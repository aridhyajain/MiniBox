# tests/init_test.sh

## Purpose
End-to-end check of `minibox run --init`. Run with `sudo bash tests/init_test.sh`; it never calls sudo itself. Needs `./minibox`,
the Alpine rootfs and the host tools `ip`, `timeout`, `awk`. Takes about 40 seconds. It sets `MINIBOX_STATE_DIR` to a `mktemp -d`
directory removed at the end; an `EXIT` trap runs `rm -f` on every container still listed. It refuses to start if `box-*` cgroups
or `vethh*` interfaces already exist. Every minibox call goes through `timeout 30` (the `mb`, `run_fg`, `run_d` helpers), and the
benchmark-hang check through `timeout 10`, so a hang is a failure, not a stuck test. It sources `tests/lib.sh` and is the last script in `tests/run_all.sh`.
`INFO:` lines are control measurements (the same thing without `--init`); they never fail the script.

## Functions
| Function | Purpose |
|----------|---------|
| `report name status [detail]` | prints `PASS:`/`FAIL:`, counts failures |
| `mb args...` | `timeout 30 minibox args`, stdin closed |
| `run_fg [options] -- cmd...` | foreground `minibox run --rootfs rootfs ...` (exit status = minibox's) |
| `run_d [options] -- cmd...` | detached run, prints the ID |
| `exec_in target cmd...` | `minibox exec target -- cmd...` |
| `field id key`, `wait_status id status seconds` | read / poll the state file |
| `elapsed start`, `below value limit` | wall-clock seconds since a `date +%s.%N` value; float comparison with `awk` |
| `veth_count`, `wait_no_veth` | count / wait for `vethh*` interfaces |
| `live_count`, `zombie_count`, `wait_live`, `report_live` | from `tests/lib.sh` (zombies = `pids.current` − live processes in `cgroup.procs`) |
| `cleanup` | EXIT trap: `rm -f` all containers, delete the temp dir |

## Checks
- **a. PID 1** – `run -d --init -- sleep 300`: `/proc/1/comm` is `minibox-init`, `/proc/2/comm` is `sleep`, and PID 2's parent is PID 1. Control without `--init`: `/proc/1/comm` is `sleep`.
  Also `ps --json` has `"init":true` / `false`, the `ps` table shows `yes` in the INIT column, the state file has `init=1` / `init=0`.
- **g. exec** – `exec echo hello`, `exec sh -c 'exit 3'` (returns 3) and `exec ps` work in a `--init` container; an `exec -- sleep 30` whose `minibox exec` is SIGKILLed leaves
  no live process and **no zombie** (the init reaps the orphan).
- **d. signal forwarding** – `stop` of a `--init` container takes < 2 s and the state records exit code 143. Control: `stop --time 1` without `--init` (reports time and exit code, expected 137, informational).
  `kill -USR1` on the init is forwarded to the command (a `trap` in `sh` prints and exits 0).
- **b. reaping** – `--memory 64M --init -- sh -c '(sleep 0.3 &); sleep 3'`: after 1.5 s `zombie_count` is 0. The same command without `--init` is reported as `INFO` (expected 1 zombie).
- **c. exit codes** – `--init`: `exit 7` → 7; self `kill -TERM` → 143; self `kill -KILL` → 137; missing program → 127 with a message; `/etc/passwd` (not executable) → 126; output reaches stdout.
- **e. benchmark hang** – `timeout 10 minibox run --init -- timeout 2 sh -c 'while :; do :; done'` finishes in < 6 s.
- **f. limits and network** – `--init --memory 64M --cpus 0.5 --pids 20 --stats` runs and prints stats; `--init --pids 2` works; `--init --pids 1` is refused ("at least 2");
  `--init --net veth`: `eth0` has the address from the state file, the container pings the host end, `stop` gives 143.
- **cleanup** – after `rm -f` of everything: no state directory, no `box-*` cgroup, no `vethh*` interface is left.

## How to test
`sudo bash tests/init_test.sh` (expect `ALL CHECKS PASSED`), or as part of `sudo bash tests/run_all.sh`. Syntax check without root: `bash -n tests/init_test.sh`.
