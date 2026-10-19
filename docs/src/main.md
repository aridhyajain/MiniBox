# src/main.c

## Purpose
Entry point: dispatches `argv[1]` to a subcommand in `src/commands.c`. It does no parsing of its own.

`minibox run | ps | logs | stop | rm [options]`

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `static void usage(FILE *out)` | stream | – | lists the commands |
| `int main(int argc, char **argv)` | – | the subcommand's exit status | no argument → usage on stderr, 1; `help`/`--help`/`-h` → usage on stdout, 0; unknown command → message + usage, 1 |

`SUBCOMMANDS[]` is a small table of `{ name, function }`; the matching `cmd_*` gets `argc - 1, argv + 1`, so its
`argv[0]` is the subcommand name. Option parsing, the root check and the rootfs check moved to
`options.c` / `commands.c`.

## System calls used and why
None.

## OS concept
Command dispatch (like `git`/`docker`): one program, several verbs, each its own process lifetime.

## How to test
```sh
./minibox              # usage, exit 1
./minibox help         # usage, exit 0
./minibox bogus        # unknown command, exit 1
./minibox ps -x        # usage of ps
```
