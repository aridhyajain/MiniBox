# ui/static/style.css

## Purpose
Styling for the page: a clean card layout, status badges, a responsive launch form and table, and automatic light and dark
themes. No external fonts or images (system font stack).

## Functions
None (CSS). Structure:
- **Colour variables** on `:root` (light); `@media (prefers-color-scheme: dark)` overrides them, so the theme follows the system setting.
- **Layout**: centred `main` with `.card` sections; the launch form is a 3-column grid (2 columns under 720 px, 1 under 460 px); the table sits in
  `.table-wrap` with horizontal scroll on narrow screens.
- **Components**: `.badge.running|exited|dead`, `.banner` (errors), `.output` (dark log/exec box, wraps long lines, scrolls),
  `button` states (`:disabled` is greyed and shows `not-allowed`), `:focus-visible` outlines for keyboard use.
- No inline styles anywhere (the Content-Security-Policy does not allow them).

## System calls used and why
None.

## OS concept
None specific; presentation layer of the UI.

## How to test
Open the UI, then switch the OS or browser between light and dark mode and narrow the window below 720 px and 460 px: colours
change, the form reflows, and the table scrolls sideways instead of overflowing the page.
