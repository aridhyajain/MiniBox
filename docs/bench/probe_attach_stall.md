# bench/probe_attach_stall.sh

## Purpose
Shows **when** a container start with limits is slow, comparing the two ways minibox puts the container into its cgroup:
the default `clone3(CLONE_INTO_CGROUP)` (born inside the cgroup) and `MINIBOX_NO_CLONE3=1` (`clone()` then a write of the PID to
`cgroup.procs`, a cgroup *migration*). Part 1 runs seven combinations of limits, all with `--net none`, 12 timed runs each after 2 warm-ups.
Part 2 times a limited run (`--memory 64M --cpus 0.5 --pids 20`) right after a `--net veth` run, 10 pairs per mode. It measures only *when*
the slow runs happen; the kernel mechanism (RCU grace period after network teardown) is an unverified hypothesis.

Real output of one run (the second; the first was not saved) is kept untouched in `bench/results/analysis/attach_stall_probe.txt`.
How to read it: each line is `options  mode  times in ms sorted ascending`; compare the `clone3` and `migrate` lines of the same options.

## Usage
`sudo bench/probe_attach_stall.sh [output-file]`. The optional file receives the same table (via `tee`). The script refuses to run
without root, without `./minibox` or `rootfs/bin/busybox`, and if the output file already exists (so a saved result is never overwritten).
The repository root is found from the script's own location; nothing is hard-coded.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `die msg` | message | exits 1 | prints `error: msg` to stderr |
| `now` | – | nanoseconds since the epoch on stdout | runs `date` |
| `run_ms ENV opts...` | an env assignment (may be empty) and `minibox run` options | whole milliseconds on stdout | runs `./minibox run --rootfs rootfs opts -- /bin/true` (output discarded; a failing run is still timed) |
| `sorted_line values...` | numbers | them sorted ascending on one line | – |
| `env_for mode` | `clone3` or `migrate` | `MINIBOX_NO_CLONE3=1` for `migrate`, nothing otherwise | – |
| `part1` | – | prints the heading and 14 rows | starts 2 warm-ups + 12 runs per row (7 rows x 2 modes) |
| `part2` | – | prints 2 rows | starts 10 veth runs and 10 limited runs per mode |

## System calls / commands used and why
`id -u` (`geteuid`: root check); `date +%s%N` (clock, nanoseconds); `env` + `./minibox run` (`fork`/`execve`; minibox itself uses
`clone3`/`clone`, `write` to `cgroup.procs`, `pivot_root`); `sort`, `tr` (formatting); `tee` (optional output file).

## OS concept
Cgroup attach cost: moving a running process between cgroups (migration) versus creating it inside the cgroup (`clone3`); and how an
earlier, unrelated kernel operation (network-device teardown) may delay the next one (hypothesis: a pending RCU grace period).

## How to test
```sh
bash -n bench/probe_attach_stall.sh                        # syntax
bench/probe_attach_stall.sh                                # as a normal user: refuses (exit 1)
sudo bench/probe_attach_stall.sh bench/results/analysis/my_probe.txt   # a few minutes; prints and saves the table
sudo bench/probe_attach_stall.sh bench/results/analysis/attach_stall_probe.txt   # refuses: file exists, nothing changed
make probe-attach                                          # prints the sudo command
```
Expected: 14 rows in part 1 plus 2 rows in part 2. On the reference machine the migration rows after a veth run were 24 to 39 ms against
10 to 18 ms for `clone3`; other machines will differ. The sample is small and one run is not a proof.
