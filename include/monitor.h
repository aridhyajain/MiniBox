/* monitor.h - the process that runs a container, waits for it and records how it ended. */
#ifndef MINIBOX_MONITOR_H
#define MINIBOX_MONITOR_H

#include "container.h"
#include "state.h"

/*
 * Foreground `run`: this process is the monitor. Runs the container (state
 * gets pid/started_at when it starts), then deletes the state directory.
 * Returns what container_run() returns (exit status, or -1).
 */
int monitor_run_foreground(struct container_config *cfg, struct container_state *st);

/*
 * Detached `run -d`: fork a monitor that detaches (setsid, stdin from
 * /dev/null, stdout/stderr to <state_dir>/<id>/log), runs the container and
 * records status=exited, exit_code and finished_at when it ends.
 * The caller waits until the container has started, then prints the ID.
 * Returns 0 if it started; 1 after printing the log and removing the state
 * if it failed to start. The caller must have called state_create().
 */
int monitor_start_detached(struct container_config *cfg, struct container_state *st);

#endif /* MINIBOX_MONITOR_H */
