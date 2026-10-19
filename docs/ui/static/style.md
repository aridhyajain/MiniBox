# ui/static/style.css

## Purpose
Styling for the page, written for an internal tool rather than a dashboard: plain light theme (warm off-white page, near-black text,
one blue accent), square corners (2px at most), 1px borders, no shadows, gradients, blur, animation or icons. System font stacks only
(sans for labels, monospace for ids, commands, numbers and logs); no external fonts or images, so the Content-Security-Policy
(`default-src 'self'`) is satisfied.

## Functions
None (CSS). Structure:
- **Palette** as variables on `:root` (`--bg`, `--panel`, `--head`, `--text`, `--muted`, `--border`, `--rule`, `--accent`, `--selected`, `--danger`,
  `--err-bg`, `--placeholder` (#757168: placeholder text, 4.8:1 on the field background `--panel`, clearly lighter than typed text `--text`, 17:1), three state-square colours, two font stacks). The accent is used only for the primary button, links, focus rings and the selected-row marker.
  There is no dark theme.
- **Run form**: `#launch-form` is a grid (`repeat(auto-fill, minmax(11rem, 1fr))`, 2 columns under 640 px, 1 under 420 px); `label` puts a small caption above its
  field; `.wide` spans the row; the label holding the network select (`label:has(> #f-net)`) spans 2 columns so "none (own empty namespace)" is not cut off (reset to 1 column under 420 px, where the select shrinks with the grid); `label.check` is a flex row so the Init checkbox and its text sit on one line, flush with the grid's left edge; `.actions` is the button row.
- **Containers table**: `.table-wrap` is the scroll box (both directions, `max-height` 28rem), so the page itself never scrolls sideways; the header is
  `position: sticky`; `.num` columns (memory, cpu, pids) are right-aligned with tabular digits; `.state` draws an 8x8 square (`.state-running|exited|dead|failed|other`)
  before plain text; `.name` / `.cmd` are the two lines of the "name / command" cell (command truncated with an ellipsis, full text in the `title`);
  `tr.selected` has a tint and a 3px accent bar.
- **Buttons**: flat with a 1px border; `.primary` is solid accent; `.danger` (remove) has red text and border on the normal background, so it is distinct but not filled;
  `:disabled` is grey and `not-allowed`.
- **Other**: `.banner` (errors), `.output` (logs and exec output, light box, wraps and scrolls), `.exec` row, `.muted`, `.mono`.
- **Focus**: the only highlight is a 2px accent outline on `:focus-visible` (inputs, selects, buttons, output boxes).
- Contrast of every text/background pair was computed (WCAG AA, 4.5:1): the lowest is muted text on the table header, 5.5:1; the state squares are
  not text (3:1 needed, lowest 4.3:1). No inline styles anywhere (the CSP does not allow them).

## System calls used and why
None.

## OS concept
None specific; presentation layer of the UI.

## How to test
Open the UI with zero containers and with several (running, exited 0, exited 137): the table has 8 columns, numbers line up on the right, the header stays
visible when the table scrolls. Tab through the page: every control shows the blue outline. Narrow the window to about 380 px: the form becomes one column,
the page has no horizontal scrollbar, and only the table box scrolls sideways. A screenshot check can be done with headless Chrome on a copy of the page with a stubbed `fetch`.
