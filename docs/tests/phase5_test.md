# tests/phase5_test.sh

## Purpose
End-to-end check of the web UI's HTTP API. Run as root (`sudo tests/phase5_test.sh`, about 40 s). It needs `scripts/setup_ui.sh` run first.
It starts `ui/app.py` on a random free port with a known token and a temporary `MINIBOX_STATE_DIR`, talks to it with `curl`, and
stops the server at the end. It sources `tests/lib.sh` (`live_count`, `report_live`, `note_zombies`) (also on Ctrl-C or failure, through an `EXIT` trap that removes any leftover container).

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `veth_count` | – | number of `vethh*` interfaces | – |
| `cleanup` | – | – | kills the server, `rm -f` of leftover containers, removes temp dirs |
| `report name rc [detail]` | check name, status | prints `PASS:`/`FAIL:` | counts failures |
| `wait_no_veth` | – | 0 once no `vethh*` remains (up to 3 s) | – |
| `req METHOD path [json]` | – | sets `CODE` and `BODY` | uses the right token |
| `jget expr` | python expression on `d` | prints it | reads `BODY` as JSON |
| `plain_code [curl args] url` | – | prints the HTTP status | no token unless given |
| `bad_launch desc json` | – | one check | expects 400 |

## Checks
1. **Auth / Host**: no token, wrong token, token plus extra characters, non-Bearer scheme, token in the query string: all 401. A bad `Host` is
   rejected (400) with the right token and for the page. `localhost:<port>` is accepted. The page loads without a token. No `Access-Control-*` header.
2. **Socket**: `ss -ltnH "sport = :<port>"` shows exactly `127.0.0.1:<port>`.
3. **Validation (400)**: bad name, `;` in name, bad/negative/huge memory, cpus 9999 and 0, pids too large and negative, bad net, bad hostname,
   empty/blank/missing command, newline in command, 33 arguments, `rootfs` from the browser, non-string command, non-object body, non-JSON body;
   malformed ID (400); unknown well-formed ID (404 for exec/logs); body over 16 KB (413); POST without JSON content type (400); no container created.
4. **Launch/list/logs**: 201 and a 12-hex ID; the cgroup exists; listing shows running, name, hostname, limits (`64M` = 67108864 bytes, 0.5 CPU, 32 pids) and
   the veth IP; logs contain the echoed line; `tail=0` is 400; a duplicate name is 409.
5. **Exec**: `hostname` returns `uibox`; stdout, stderr and exit code 7; `echo $(id) ; touch ...` is not interpreted by a shell (literal text, no file created);
   `sleep 30` is stopped after 10 s with `timed_out` true and is no longer running in the container. The check compares the number of **live** processes (lines of `box-<id>/cgroup.procs`) with the count taken before the exec, not `pids.current`, because the killed `sleep` can remain as a zombie child of the container's PID 1, which never reaps (see `docs/tests/lib.md`). A failure prints every process left in the cgroup; an `INFO:` line shows the unreaped zombie count.
   The related `minibox exec` signal behaviour is tested in `tests/phase4b_test.sh`.
6. **Stop/remove**: stop gives 200 and status `exited`; exec afterwards gives 409; DELETE 200; list empty; second DELETE 404; DELETE on a running container
   works; the 11th running container is refused (409).
7. **Cleanup**: no state directory entries, no `box-*` cgroup, no `vethh*` interface, and the server process is gone.

Exit status: 0 if all checks pass, 1 otherwise (the failing checks and the last server log lines, with the token hidden, are printed).

## System calls used and why
Through the tools it calls: `curl` (sockets), `ss` (reads socket tables via netlink), `ip` (netlink), `kill`/`wait` for the server process,
`mktemp`. The server uses `bind`/`listen` on loopback.

## OS concept
Testing a privileged service from the outside: authentication and network exposure checks, and verifying that the cgroup, network interface and
on-disk state of every container are released.

## How to test
```sh
scripts/setup_ui.sh             # once, as your normal user
sudo tests/phase5_test.sh
sudo bash tests/run_all.sh      # includes it
```
