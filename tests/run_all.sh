#!/usr/bin/env bash
# run_all.sh - runs every minibox test script in order and prints a summary.
# Usage: sudo bash tests/run_all.sh [-v]
#   -v   show the full output of each script (default: only PASS/FAIL lines)
# Scripts that do not exist yet are skipped. Exit status: 0 if none failed, 1 otherwise.

TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPTS="phase1_test.sh phase2_test.sh phase3_test.sh phase4_test.sh phase4b_test.sh"
VERBOSE=0

if [ "$(id -u)" -ne 0 ]; then
    echo "run_all.sh must run as root: sudo bash tests/run_all.sh"
    exit 1
fi
case "$1" in
    "")  ;;
    -v)  VERBOSE=1 ;;
    *)   echo "Usage: sudo bash tests/run_all.sh [-v]"; exit 1 ;;
esac
if [ ! -x "$TESTS_DIR/../minibox" ]; then
    echo "minibox not built; run 'make' first."
    exit 1
fi

LOG_DIR=$(mktemp -d)
trap 'rm -rf "$LOG_DIR"' EXIT

PASSED=0
FAILED=0
SKIPPED=0
FAILED_NAMES=""

for script in $SCRIPTS; do
    if [ ! -f "$TESTS_DIR/$script" ]; then
        echo "SKIP: $script (does not exist yet)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    start=$(date +%s)
    if [ "$VERBOSE" -eq 1 ]; then
        echo "==== $script ===="
        bash "$TESTS_DIR/$script" 2>&1 | tee "$LOG_DIR/$script.log"
        code=${PIPESTATUS[0]}
    else
        bash "$TESTS_DIR/$script" >"$LOG_DIR/$script.log" 2>&1
        code=$?
    fi
    secs=$(($(date +%s) - start))
    if [ "$code" -eq 0 ]; then
        echo "PASS: $script (${secs}s)"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL: $script (exit $code, ${secs}s)"
        if [ "$VERBOSE" -eq 0 ]; then
            # the failing checks, or the last lines if the script stopped before any check
            if grep -q '^FAIL' "$LOG_DIR/$script.log"; then
                grep '^FAIL' "$LOG_DIR/$script.log" | sed 's/^/    /'
            else
                tail -n 5 "$LOG_DIR/$script.log" | sed 's/^/    /'
            fi
        fi
        FAILED=$((FAILED + 1))
        FAILED_NAMES="$FAILED_NAMES $script"
    fi
done

echo
echo "Summary: $PASSED passed, $FAILED failed, $SKIPPED skipped"
if [ "$FAILED" -gt 0 ]; then
    echo "Failed:$FAILED_NAMES"
    echo "Re-run one with: sudo tests/<script>   (add -v here to see everything)"
    exit 1
fi
echo "ALL TEST SCRIPTS PASSED"
exit 0
