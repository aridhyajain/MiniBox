# ui/templates/index.html

## Purpose
The single page of the web UI: static markup only. It contains the launch form, the containers table, the empty-state
message, a (hidden until used) detail panel with logs and an exec box, and an error banner. It loads only
`/static/style.css` and `/static/app.js` from the same origin (no CDN, no fonts, no inline scripts or styles), so it works offline
and satisfies the Content-Security-Policy `default-src 'self'`. All dynamic content is filled in by `app.js`.

## Functions
None (markup). Elements `app.js` relies on, by `id`:
| id | Role |
|----|------|
| `banner` | error messages (`role="alert"`, hidden when empty) |
| `launch-form`, `f-command`, `f-name`, `f-hostname`, `f-memory`, `f-cpus`, `f-pids`, `f-net`, `launch-btn` | launch form |
| `empty`, `containers`, `rows` | empty state, table, table body |
| `detail`, `detail-title`, `detail-close` | detail panel for the selected container |
| `logs`, `logs-refresh` | log output (last 200 lines) and its refresh button |
| `exec-form`, `exec-command`, `exec-btn`, `exec-status`, `exec-out` | exec box |

Flask renders it with `render_template` (there are no template variables, so nothing from the server is interpolated into HTML).

## System calls used and why
None.

## OS concept
Browser security model: same-origin loading, Content-Security-Policy, `Referrer-Policy: no-referrer` (also set as a `<meta>`).

## How to test
Start the UI (`sudo scripts/run_ui.sh 8080`), open the printed URL, and check the page loads with the browser's network tab
showing requests only to `127.0.0.1:<port>`. `curl -s http://127.0.0.1:<port>/` returns this HTML without a token.
