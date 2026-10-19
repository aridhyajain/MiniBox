/* minit.h - the tiny init (PID 1) used by `minibox run --init`. */
#ifndef MINIBOX_MINIT_H
#define MINIBOX_MINIT_H

/*
 * minit_run - become the container's init: start argv as a child, forward
 * signals to it, reap every orphan, and finish with the child's status.
 *
 * Must be called inside the container as PID 1, after pivot_root and all other
 * setup, instead of exec'ing the command directly.
 *
 * Returns the exit status for PID 1 to exit with: the child's exit code, or
 * 128+N if it was killed by signal N; 127 if fork() failed or the command was
 * not found; 126 if it could not be executed.
 */
int minit_run(char *const argv[]);

#endif /* MINIBOX_MINIT_H */
