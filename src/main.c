/*
 * main.c - subcommand dispatch for minibox.
 *
 * Usage: minibox run | ps | logs | stop | rm | exec  [options]
 * Each subcommand parses its own arguments (see commands.c).
 */
#include "commands.h"
#include "exec.h"

#include <stdio.h>
#include <string.h>

struct subcommand {
    const char *name;
    int (*run)(int argc, char **argv);
};

static const struct subcommand SUBCOMMANDS[] = {
    { "run",  cmd_run },
    { "ps",   cmd_ps },
    { "logs", cmd_logs },
    { "stop", cmd_stop },
    { "rm",   cmd_rm },
    { "exec", cmd_exec },
};
#define N_SUBCOMMANDS (sizeof(SUBCOMMANDS) / sizeof(SUBCOMMANDS[0]))

static void usage(FILE *out)
{
    fprintf(out,
            "Usage: minibox <command> [options]\n"
            "Commands:\n"
            "  run    start a container (add -d to run it in the background)\n"
            "  ps     list containers\n"
            "  logs   show the output of a background container\n"
            "  stop   stop a running container\n"
            "  rm     remove a stopped (or dead) container\n"
            "  exec   run a command inside a running container\n"
            "Run 'minibox <command>' without arguments for the options of a command.\n");
}

int main(int argc, char **argv)
{
    size_t i;

    if (argc < 2) {
        usage(stderr);
        return 1;
    }
    if (strcmp(argv[1], "help") == 0 || strcmp(argv[1], "--help") == 0 ||
        strcmp(argv[1], "-h") == 0) {
        usage(stdout);
        return 0;
    }
    for (i = 0; i < N_SUBCOMMANDS; i++)
        if (strcmp(argv[1], SUBCOMMANDS[i].name) == 0)
            return SUBCOMMANDS[i].run(argc - 1, argv + 1);
    fprintf(stderr, "minibox: unknown command '%s'\n", argv[1]);
    usage(stderr);
    return 1;
}
