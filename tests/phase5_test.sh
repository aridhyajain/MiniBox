#!/usr/bin/env bash
# phase5_test.sh - checks the web UI's HTTP API: authentication, Host check, loopback-only
# socket, input validation, and the full launch / list / logs / exec / stop / remove cycle.
# Usage: sudo tests/phase5_test.sh     (run from anywhere; takes about 40 seconds)
# Needs the UI environment first: scripts/setup_ui.sh (as your normal user).
# Starts the server on a random free port with a known token, uses a temporary state
# directory (MINIBOX_STATE_DIR), and stops the server at the end.
# Exit status: 0 if all checks pass, 1 otherwise.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MB="$ROOT_DIR/minibox"
ROOTFS="$ROOT_DIR/rootfs"
PY="$ROOT_DIR/ui/venv/bin/python"
CG_BASE=/sys/fs/cgroup/minibox
FAILS=0

# shellcheck source=lib.sh
. "$ROOT_DIR/tests/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root: sudo tests/phase5_test.sh"
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
if [ ! -x "$PY" ] || ! "$PY" -c "import flask" 2>/dev/null; then
    echo "UI environment missing; run 'scripts/setup_ui.sh' first (as your normal user, no sudo)."
    exit 1
fi
for tool in ip curl ss python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "host tool '$tool' missing"; exit 1; }
done

veth_count() {
    ip -o link show | grep -c 'vethh'
}

if ls -d "$CG_BASE"/box-* >/dev/null 2>&1 || [ "$(veth_count)" -ne 0 ]; then
    echo "Leftover box-* cgroups or vethh* interfaces exist (earlier run?). Remove them first:"
    ls -d "$CG_BASE"/box-* 2>/dev/null
    ip -o link show | grep vethh
    exit 1
fi

STATE=$(mktemp -d)
TMP=$(mktemp -d)
export MINIBOX_STATE_DIR="$STATE"
SERVER_PID=""

# Stop the server, remove whatever container is left (even after a failed check or Ctrl-C).
cleanup() {
    local id
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
    for id in $("$MB" ps -a 2>/dev/null | awk 'NR>1 {print $1}'); do
        "$MB" rm -f "$id" >/dev/null 2>&1
    done
    rm -rf "$STATE" "$TMP"
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

wait_no_veth() {
    local i
    for i in $(seq 1 30); do
        [ "$(veth_count)" -eq 0 ] && return 0
        sleep 0.1
    done
    return 1
}

# ---- server on a random free port, with a known token ----
PORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
BASE="http://127.0.0.1:$PORT"
MINIBOX_UI_TOKEN="$TOKEN" "$PY" "$ROOT_DIR/ui/app.py" --port "$PORT" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 50); do
    curl -s -o /dev/null "$BASE/" && break
    sleep 0.2
done
if ! curl -s -o /dev/null "$BASE/"; then
    echo "FAIL: the UI server did not start"
    cat "$TMP/server.log"
    exit 1
fi

# req <METHOD> <path> [json-body]: sets CODE (HTTP status) and BODY, using the right token.
req() {
    local args=(-s -m 40 -o "$TMP/body" -w '%{http_code}' -X "$1" -H "Authorization: Bearer $TOKEN")
    [ -n "$3" ] && args+=(-H 'Content-Type: application/json' -d "$3")
    CODE=$(curl "${args[@]}" "$BASE$2")
    BODY=$(cat "$TMP/body")
}

# jget <python expression on d>: read the last BODY as JSON, e.g. jget 'd["id"]'.
jget() {
    echo "$BODY" | python3 -c "import sys, json; d = json.load(sys.stdin); print($1)" 2>/dev/null
}

# plain_code [curl args...] <url>: HTTP status without the token (for the rejection checks).
plain_code() {
    curl -s -m 10 -o /dev/null -w '%{http_code}' "$@"
}

# ---- 1. authentication and Host check ----
code=$(plain_code "$BASE/api/containers")
[ "$code" = 401 ]
report "no token gives 401" $? "got $code"

code=$(plain_code -H "Authorization: Bearer wrong-token" "$BASE/api/containers")
[ "$code" = 401 ]
report "a wrong token gives 401" $? "got $code"

code=$(plain_code -H "Authorization: Bearer ${TOKEN}x" "$BASE/api/containers")
[ "$code" = 401 ]
report "a token with extra characters gives 401" $? "got $code"

code=$(plain_code -H "Authorization: Basic $TOKEN" "$BASE/api/containers")
[ "$code" = 401 ]
report "the token in a non-Bearer scheme gives 401" $? "got $code"

code=$(plain_code "$BASE/api/containers?token=$TOKEN")
[ "$code" = 401 ]
report "the token in the query string is not accepted for the API" $? "got $code"

code=$(plain_code -H "Authorization: Bearer $TOKEN" -H "Host: evil.example.com" "$BASE/api/containers")
[ "$code" = 400 ]
report "a bad Host header is rejected even with the right token (DNS rebinding)" $? "got $code"

code=$(plain_code -H "Host: evil.example.com:$PORT" "$BASE/")
[ "$code" = 400 ]
report "a bad Host header is rejected for the page too" $? "got $code"

code=$(plain_code -H "Authorization: Bearer $TOKEN" -H "Host: localhost:$PORT" "$BASE/api/containers")
[ "$code" = 200 ]
report "Host 'localhost:<port>' is accepted" $? "got $code"

code=$(plain_code "$BASE/")
[ "$code" = 200 ]
report "the page itself loads without a token (it reads the token from the URL)" $? "got $code"

headers=$(curl -s -m 10 -D - -o /dev/null -H "Origin: http://evil.example.com" \
          -H "Authorization: Bearer $TOKEN" "$BASE/api/containers")
echo "$headers" | grep -qi '^access-control-'
[ $? -ne 0 ]
report "no CORS headers are sent" $?

# ---- 2. the socket is loopback only ----
listen=$(ss -ltnH "sport = :$PORT" | awk '{print $4}')
[ "$listen" = "127.0.0.1:$PORT" ]
report "the listening socket is bound to 127.0.0.1:$PORT only" $? "ss shows: $listen"

# ---- 3. invalid input is rejected before anything starts ----
bad_launch() { # bad_launch <description> <json>
    req POST /api/containers "$2"
    [ "$CODE" = 400 ]
    report "launch rejected with 400: $1" $? "got $CODE: $BODY"
}
bad_launch "name with uppercase and space"   '{"command":"sleep 5","name":"Bad Name"}'
bad_launch "name with ; injection"           '{"command":"sleep 5","name":"a;rm -rf /"}'
bad_launch "memory in the wrong format"      '{"command":"sleep 5","memory":"lots"}'
bad_launch "negative memory"                 '{"command":"sleep 5","memory":"-5M"}'
bad_launch "huge memory"                     '{"command":"sleep 5","memory":"999999G"}'
bad_launch "cpus above the core count"       '{"command":"sleep 5","cpus":"9999"}'
bad_launch "cpus zero"                       '{"command":"sleep 5","cpus":"0"}'
bad_launch "pids too large"                  '{"command":"sleep 5","pids":"100000"}'
bad_launch "pids negative"                   '{"command":"sleep 5","pids":-3}'
bad_launch "net not none or veth"            '{"command":"sleep 5","net":"host"}'
bad_launch "hostname with a space and a command substitution" '{"command":"sleep 5","hostname":"a $(id)"}'
bad_launch "empty command"                   '{"command":""}'
bad_launch "blank command"                   '{"command":"   "}'
bad_launch "missing command"                 '{"name":"x"}'
bad_launch "command with a newline"          '{"command":"echo hi\nrm -rf /"}'
bad_launch "33 arguments"                    "{\"command\":\"$(python3 -c 'print(" ".join(["a"] * 33))')\"}"
bad_launch "rootfs path from the browser"    '{"command":"sleep 5","rootfs":"/"}'
bad_launch "command is not a string"         '{"command":["sleep","5"]}'
bad_launch "body is not an object"           '[1,2,3]'
bad_launch "body is not JSON"                'not json'

req POST /api/containers/zzzz/exec '{"command":"ls"}'
[ "$CODE" = 400 ]
report "a container ID that is not 12 hex characters is rejected with 400" $? "got $CODE"

req POST /api/containers/0123456789ab/exec '{"command":"ls"}'
[ "$CODE" = 404 ]
report "exec on an unknown (well-formed) ID gives 404" $? "got $CODE: $BODY"

req GET /api/containers/0123456789ab/logs
[ "$CODE" = 404 ]
report "logs for an unknown ID gives 404" $? "got $CODE"

big=$(python3 -c 'print("{\"command\":\"" + "a" * 40000 + "\"}")')
req POST /api/containers "$big"
[ "$CODE" = 413 ]
report "a request body over the size cap gives 413" $? "got $CODE"

curl -s -m 10 -o "$TMP/body" -w '%{http_code}' -X POST -H "Authorization: Bearer $TOKEN" \
     -d '{"command":"ls"}' "$BASE/api/containers" >"$TMP/code"
[ "$(cat "$TMP/code")" = 400 ]
report "a POST that is not application/json is rejected with 400" $? "got $(cat "$TMP/code")"

ids=$("$MB" ps -a | awk 'NR>1 {print $1}' | wc -l)
[ "$ids" -eq 0 ]
report "none of the rejected requests created a container" $? "$ids container(s) exist"

# ---- 4. launch, list, logs ----
req POST /api/containers '{"command":"sh -c \"echo hello-from-ui; sleep 300\"","name":"uitest","hostname":"uibox","memory":"64M","cpus":"0.5","pids":"32","net":"veth"}'
cid=$(jget 'd["id"]')
[ "$CODE" = 201 ] && echo "$cid" | grep -Eq '^[0-9a-f]{12}$'
report "launch returns 201 and a 12-hex ID" $? "got $CODE: $BODY"
[ -d "$CG_BASE/box-$cid" ]
report "the container has its cgroup box-<id>" $?

req GET /api/containers
[ "$CODE" = 200 ] && [ "$(jget '[c["status"] for c in d["containers"] if c["id"] == "'"$cid"'"][0]')" = running ]
report "GET /api/containers lists it as running" $? "got $CODE: $BODY"
fields=$(jget '(lambda c: "%s %s %s %s %s" % (c["name"], c["hostname"], c["memory"], c["cpus"], c["pids"]))([c for c in d["containers"] if c["id"] == "'"$cid"'"][0])')
[ "$fields" = "uitest uibox 67108864 0.5 32" ]
report "the listing shows the name, hostname and limits that were asked for" $? "got: $fields"
ip_c=$(jget '[c["ip"] for c in d["containers"] if c["id"] == "'"$cid"'"][0]')
echo "$ip_c" | grep -Eq '^10\.0\.[0-9]+\.2$'
report "the listing shows the veth IP" $? "got '$ip_c'"

logs_ok=1
for _ in $(seq 1 25); do
    req GET "/api/containers/$cid/logs?tail=50"
    if [ "$CODE" = 200 ] && [ "$(jget '"hello-from-ui" in d["logs"]')" = True ]; then
        logs_ok=0
        break
    fi
    sleep 0.2
done
report "logs returns the container's output" $logs_ok "got $CODE: $BODY"

req GET "/api/containers/$cid/logs?tail=0"
[ "$CODE" = 400 ]
report "logs?tail=0 is rejected with 400" $? "got $CODE"

req POST /api/containers '{"command":"sleep 5","name":"uitest"}'
[ "$CODE" = 409 ]
report "launching with a name that is in use gives 409" $? "got $CODE: $BODY"

# ---- 5. exec ----
req POST "/api/containers/$cid/exec" '{"command":"hostname"}'
[ "$CODE" = 200 ] && [ "$(jget 'd["output"].strip()')" = uibox ] && [ "$(jget 'd["exit_code"]')" = 0 ]
report "exec -- hostname returns 'uibox' and exit code 0" $? "got $CODE: $BODY"

req POST "/api/containers/$cid/exec" '{"command":"sh -c \"echo out; echo err >&2; exit 7\""}'
[ "$CODE" = 200 ] && [ "$(jget 'd["exit_code"]')" = 7 ] && [ "$(jget '"out" in d["output"] and "err" in d["output"]')" = True ]
report "exec returns stdout, stderr and the exit code 7" $? "got $CODE: $BODY"

req POST "/api/containers/$cid/exec" '{"command":"echo $(id) ; touch /tmp/not-a-shell"}'
[ "$CODE" = 200 ] && [ "$(jget '"$(id)" in d["output"]')" = True ]
report "shell syntax is not interpreted: '\$(id)' comes back literally" $? "got $CODE: $BODY"
[ ! -e /tmp/not-a-shell ] && [ ! -e "$ROOTFS/tmp/not-a-shell" ]
report "the '; touch ...' part did not run on the host" $?

live_base=$(live_count "$cid")   # live processes before the exec (the container's own sh/sleep)
req POST "/api/containers/$cid/exec" '{"command":"sleep 30"}'
[ "$CODE" = 200 ] && [ "$(jget 'd["timed_out"]')" = True ]
report "a command that runs too long is stopped after 10 s and reported as timed_out" $? "got $CODE: $BODY"
sleep 0.5
# Live processes (cgroup.procs), not pids.current: the killed sleep may linger as a zombie.
[ "$(live_count "$cid")" -le "$live_base" ]
report_live "the timed-out 'sleep 30' is no longer running inside the container" $? "$cid" "$live_base"
note_zombies "$cid"

# ---- 6. stop and remove ----
req POST "/api/containers/$cid/stop"
[ "$CODE" = 200 ]
report "stop returns 200" $? "got $CODE: $BODY"
req GET /api/containers
[ "$(jget '[c["status"] for c in d["containers"] if c["id"] == "'"$cid"'"][0]')" = exited ]
report "the container is listed as exited after stop" $? "got $BODY"

req POST "/api/containers/$cid/exec" '{"command":"true"}'
[ "$CODE" = 409 ]
report "exec on a stopped container gives 409" $? "got $CODE: $BODY"

req DELETE "/api/containers/$cid"
[ "$CODE" = 200 ]
report "DELETE returns 200" $? "got $CODE: $BODY"
req GET /api/containers
[ "$CODE" = 200 ] && [ "$(jget 'len(d["containers"])')" = 0 ]
report "the container list is empty again" $? "got $BODY"
req DELETE "/api/containers/$cid"
[ "$CODE" = 404 ]
report "removing it a second time gives 404" $? "got $CODE"

# A running container can be removed in one step (rm -f).
req POST /api/containers '{"command":"sleep 300","net":"veth"}'
cid2=$(jget 'd["id"]')
req DELETE "/api/containers/$cid2"
[ "$CODE" = 200 ]
report "DELETE on a running container stops and removes it" $? "got $CODE: $BODY"

# ---- 7. limit on running containers ----
for i in $(seq 1 10); do
    req POST /api/containers '{"command":"sleep 300"}'
    [ "$CODE" = 201 ] || break
done
req POST /api/containers '{"command":"sleep 300"}'
[ "$CODE" = 409 ] && [ "$(jget 'd["error"]["message"]')" != "" ]
report "an 11th running container is refused with 409" $? "got $CODE: $BODY"
for id in $("$MB" ps -a | awk 'NR>1 {print $1}'); do
    req DELETE "/api/containers/$id"
done

# ---- 8. nothing left behind ----
left=$(ls -A "$STATE" | grep -v '^\.lock$')
[ -z "$left" ]
report "no container state is left" $? "left: $left"
ls -d "$CG_BASE"/box-* >/dev/null 2>&1
[ $? -ne 0 ]
report "no box-* cgroup is left" $? "$(ls -d "$CG_BASE"/box-* 2>/dev/null)"
wait_no_veth
report "no vethh* interface is left" $? "$(ip -o link show | grep vethh)"

kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null
SERVER_PID=""
curl -s -m 2 -o /dev/null "$BASE/"
[ $? -ne 0 ]
report "the server process was stopped" $?

echo
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
fi
echo "$FAILS CHECK(S) FAILED"
echo "server log (token hidden):"
sed 's/token=[^ ]*/token=<hidden>/' "$TMP/server.log" | tail -n 20
exit 1
