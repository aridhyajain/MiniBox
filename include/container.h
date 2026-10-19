/* container.h - create and wait for an isolated container process. */
#ifndef MINIBOX_CONTAINER_H
#define MINIBOX_CONTAINER_H

struct container_config {
    const char *rootfs;        /* absolute path of the root filesystem directory */
    const char *hostname;      /* hostname seen inside the container (UTS ns) */
    char *const *argv;         /* command to run, NULL-terminated, argv[0] = program */
};

/*
 * container_run - start the command in new PID, UTS and mount namespaces
 * and wait for it to finish.
 *
 * Returns the container command's exit status (128+N if killed by signal N),
 * or -1 if the container could not be created.
 */
int container_run(const struct container_config *cfg);

#endif /* MINIBOX_CONTAINER_H */
