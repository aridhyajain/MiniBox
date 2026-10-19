/* rootfs.h - switch a process into a new root filesystem (mount namespace). */
#ifndef MINIBOX_ROOTFS_H
#define MINIBOX_ROOTFS_H

/*
 * rootfs_setup - prepare mounts and pivot_root into `rootfs`.
 *
 * Must run inside a new mount namespace (CLONE_NEWNS) and, for /proc to show
 * only container processes, inside a new PID namespace as well.
 * `rootfs` must be an absolute path to a directory.
 *
 * Returns 0 on success, -1 on error (message already printed with perror).
 */
int rootfs_setup(const char *rootfs);

#endif /* MINIBOX_ROOTFS_H */
