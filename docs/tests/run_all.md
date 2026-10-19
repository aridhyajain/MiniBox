# tests/run_all.sh

## Purpose
One command that runs every test script in order and prints one `PASS`/`FAIL` line per script plus a summary:
`phase1_test.sh`, `phase2_test.sh`, `phase3_test.sh`, `phase4_test.sh`, `phase4b_test.sh`, `phase5_test.sh` (the web UI; it needs `scripts/setup_ui.sh` run first, otherwise it fails with that hint), `phase6_test.sh` (benchmark tooling, about 1 minute), `init_test.sh` (`minibox run --init`, about 40 seconds). A script that does not exist is
skipped (`SKIP:` line). Must be run as root (it refuses otherwise); it never calls sudo. `make test-all` prints the command.

## Usage
```sh
sudo bash tests/run_all.sh       # PASS/FAIL per script; the failing checks are listed under a failed script
sudo bash tests/run_all.sh -v    # also show every script's full output
```
Exit status: 0 if no script failed, 1 if any failed (or on a usage / not-root / not-built error). Takes a few minutes.

## Details
- Each script runs as `bash <script>` with its output in a temp directory (`mktemp -d`, removed by an `EXIT` trap).
  Without `-v` a failed script's `FAIL` lines (or its last five lines if it stopped before any check) are printed.
- Counters: passed, failed, skipped; the summary names the failed scripts.
- Refuses to run if `minibox` is not built.

## System calls used and why
None directly (shell script: `fork`/`exec` for each test script, `mktemp`, `date`).

## OS concept
Process exit statuses as the interface between a test runner and its tests.

## How to test
```sh
bash tests/run_all.sh          # as a normal user: refuses, exit 1
sudo bash tests/run_all.sh     # runs everything
```
