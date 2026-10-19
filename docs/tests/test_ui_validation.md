# tests/test_ui_validation.py

## Purpose
Unit tests (`unittest`, no root, no Flask, no containers) for `ui/validation.py`. 37 tests cover valid values, boundaries, and
hostile input for every field.

## Functions
Test classes (each method is one test case): `NameTests`, `HostnameTests`, `MemoryTests`, `CpusTests`, `PidsTests`, `NetTests`,
`CommandTests`, `ContainerIdTests`, `TailTests`, `LaunchPayloadTests`, `ExecPayloadTests`. Shared data: `INJECTIONS`, a list of
injection-style strings (`; rm -rf /`, `$(id)`, backticks, newline, carriage return, NUL, `../../etc/passwd`, `--rootfs=/`, `a&&b`,
`<script>`...) that every non-command field must reject.

Highlights:
- Numbers: negative, zero, huge (`999999G`, `10**12`), `nan`, `inf`, `1e9`, hex, booleans, floats where integers are required.
- Command: `; rm -rf /`, `$(id)`, `` `id` `` and `a && b | c > d` become **literal arguments** (no shell would interpret them);
  newlines, tabs, NUL and ESC are rejected; empty/blank/`''` rejected; unbalanced quotes rejected; 32 arguments allowed, 33 rejected; a
  256-character argument allowed, 257 rejected.
- IDs and paths: `../../etc/passwd`, a trailing newline, upper-case hex and wrong lengths are rejected.
- Launch payload: unknown fields such as `rootfs` are rejected, so the browser can never choose the rootfs.

## System calls used and why
None.

## OS concept
Input validation as a security control for a privileged service (whitelist, not blacklist).

## How to test
```sh
python3 -m unittest tests/test_ui_validation.py -v
```
