# ui/static/app.js

## Purpose
All behaviour of the page, in plain JavaScript (one IIFE, no libraries, no external requests). It talks only to the
same-origin `/api`. **Everything that comes from the server or from a container is written with `textContent` /
`createElement`; `innerHTML` is never used**, so container output cannot inject markup or script.

## Functions
| Function | Inputs | Returns | Behaviour |
|----------|--------|---------|-----------|
| `loadToken()` | – | token string | takes `?token=` from the URL, stores it in `sessionStorage`, removes it from the address bar with `history.replaceState`; otherwise reads `sessionStorage` |
| `api(method, path, body)` | HTTP method, path, optional object | Promise of decoded JSON | adds `Authorization: Bearer <token>`; on an error status throws an `Error` whose message is the API's message (prefixed with the field name); 401 gives "Not authorised. Open the URL printed by the server" |
| `showError(msg)` / `clearError()` | text | – | the red banner at the top |
| `busy(button, work)` | button, function returning a Promise | Promise | disables the button until the work finishes |
| `el(tag, class, text)` | – | element | creates an element with `textContent` |
| `formatMemory`, `formatLimits`, `formatAge` | numbers | text | `67108864` -> `64M`; `mem=64M cpu=0.5 pids=32`; `12s ago` |
| `actionButton(...)` | label, id, action, handler | button | per-row button that stays disabled while its request runs (state kept in `pending`, so the 3 s re-render does not re-enable it) |
| `renderTable()` | – | – | rebuilds the table body from `containers`; shows the empty state when there are none |
| `refresh()` | – | Promise | `GET /api/containers`; one request at a time; closes the detail panel if its container vanished |
| `stopContainer(c)`, `removeContainer(c)` | container | Promise | `POST .../stop`, `DELETE ...` (remove asks for confirmation) |
| `launch(event)` | submit event | – | sends the form fields that are filled in; empty ones are omitted so the server applies defaults |
| `select(id)`, `closeDetail()`, `updateDetailHeader()` | – | – | the detail panel below the table (it is outside the table so polling does not wipe typed text or logs) |
| `loadLogs()` | – | Promise | `GET .../logs?tail=200` into the `<pre>` |
| `runExec(event)` | submit event | – | `POST .../exec`; shows output, exit code, and a note on the 10 s time limit or the 64 KB output cut |
| `init()` | – | – | wires events, polls every 3 s (skipped while the tab is hidden), shows a clear message when no token is present |

## System calls used and why
None directly (browser APIs: `fetch`, `sessionStorage`, `history.replaceState`, `setInterval`).

## OS concept
Client side of the security design: the token never stays in the address bar, history or `Referer`, and is sent only as a header to the
same origin. Escaping of untrusted output (cross-site scripting).

## How to test
Open the UI and: launch `sh -c 'echo hi; sleep 300'`; see it appear within 3 s as `running`; open Logs and see `hi`; run `ps` in the exec
box; Stop; Remove. Launch with memory `-5M` and see the error banner. In the exec box run `echo '<img src=x onerror=alert(1)>'`
and check the text is shown literally. Open the page without `?token=` in a fresh tab and read the message.
The HTTP API itself is tested by `sudo tests/phase5_test.sh`.
