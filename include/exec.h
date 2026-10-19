/* exec.h - `minibox exec`: run a command inside a running container. */
#ifndef MINIBOX_EXEC_H
#define MINIBOX_EXEC_H

/*
 * cmd_exec - argv[0] is "exec"; the rest is `<id|name> -- COMMAND [ARGS...]`.
 *
 * Joins the container's IPC, UTS, network, PID and mount namespaces (and its
 * cgroup, if it has one) and runs COMMAND there with the caller's stdin,
 * stdout and stderr. Returns the command's exit status (128+N if killed by
 * signal N, 127 if it could not be executed), or 1 on a minibox error
 * (unknown or not running container, no command, not root, setup failure).
 *
 * The command dies with `minibox exec`: PR_SET_PDEATHSIG kills it if exec is
 * killed, and when stdin is not a terminal SIGTERM/SIGINT/SIGHUP sent to exec
 * kill the command's whole process group (exit status 128+signal).
 */
int cmd_exec(int argc, char **argv);

#endif /* MINIBOX_EXEC_H */
