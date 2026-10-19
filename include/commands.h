/* commands.h - the subcommands of the minibox CLI. */
#ifndef MINIBOX_COMMANDS_H
#define MINIBOX_COMMANDS_H

/*
 * Each cmd_* gets the arguments from the subcommand name on (argv[0] is the
 * name itself, e.g. "stop") and returns the process exit status: 0 on
 * success, 1 on any minibox error or usage error; cmd_run returns the
 * container command's exit status for foreground containers.
 */
int cmd_run(int argc, char **argv);
int cmd_ps(int argc, char **argv);
int cmd_logs(int argc, char **argv);
int cmd_stop(int argc, char **argv);
int cmd_rm(int argc, char **argv);

#endif /* MINIBOX_COMMANDS_H */
