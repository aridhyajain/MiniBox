/*
 * rootfs.c - mount setup and pivot_root.
 *
 * OS concept: every mount namespace has its own mount table. By building a
 * new table whose "/" is the Alpine directory, the container sees a different
 * filesystem tree than the host.
 *
 * Why pivot_root and not chroot?
 *  - chroot only changes where the path "/" resolves for this process. The
 *    old mounts still exist, and a root process can escape (e.g. chroot into
 *    a subdirectory, then "../" out, or use a leftover open directory fd).
 *  - pivot_root swaps the root of the whole mount namespace and moves the old
 *    root to a directory we choose. We then unmount it, so the host
 *    filesystem is genuinely gone from the container's view.
 */
#define _GNU_SOURCE
#include "rootfs.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/syscall.h>
#include <unistd.h>

#define OLD_ROOT_NAME ".pivot_old"

struct dev_node {
    const char *name;
    unsigned int major;
    unsigned int minor;
};

/* The minimal set of character devices most programs need. */
static const struct dev_node DEV_NODES[] = {
    { "null",    1, 3 },
    { "zero",    1, 5 },
    { "random",  1, 8 },
    { "urandom", 1, 9 },
    { "tty",     5, 0 },
};

/* mkdir that treats "already exists" as success. */
static int ensure_dir(const char *path, mode_t mode)
{
    if (mkdir(path, mode) == -1 && errno != EEXIST) {
        perror(path);
        return -1;
    }
    return 0;
}

/* Build "<dir>/<name>" into buf; fails if it would not fit. */
static int join_path(char *buf, size_t size, const char *dir, const char *name)
{
    int n = snprintf(buf, size, "%s/%s", dir, name);

    if (n < 0 || (size_t)n >= size) {
        fprintf(stderr, "path too long: %s/%s\n", dir, name);
        return -1;
    }
    return 0;
}

/*
 * Mount propagation: on systemd/WSL the host's "/" is a *shared* mount, so
 * mounts made in our new namespace would be copied back to the host (and the
 * umount of the old root could affect it). Marking everything private cuts
 * those event links.
 */
static int make_mounts_private(void)
{
    if (mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL) == -1) {
        perror("mount(MS_PRIVATE /)");
        return -1;
    }
    return 0;
}

/*
 * pivot_root requires the new root to be a mount point. Bind-mounting the
 * directory onto itself turns it into one.
 */
static int bind_rootfs(const char *rootfs)
{
    if (mount(rootfs, rootfs, NULL, MS_BIND | MS_REC, NULL) == -1) {
        perror("mount(bind rootfs)");
        return -1;
    }
    return 0;
}

/* Fallback when mknod is refused: bind-mount the host's device node. */
static int bind_host_device(const char *name, const char *target)
{
    char host_path[PATH_MAX];
    int fd;

    if (join_path(host_path, sizeof(host_path), "/dev", name) == -1)
        return -1;
    fd = open(target, O_CREAT | O_WRONLY, 0666);   /* mount point must exist */
    if (fd == -1) {
        perror(target);
        return -1;
    }
    close(fd);
    if (mount(host_path, target, NULL, MS_BIND, NULL) == -1) {
        perror(host_path);
        return -1;
    }
    return 0;
}

/* Create one device node, or bind-mount the host's if mknod fails. */
static int make_device(const char *dev_dir, const struct dev_node *node)
{
    char path[PATH_MAX];

    if (join_path(path, sizeof(path), dev_dir, node->name) == -1)
        return -1;
    if (mknod(path, S_IFCHR | 0666, makedev(node->major, node->minor)) == 0) {
        if (chmod(path, 0666) == -1) {     /* undo the umask */
            perror(path);
            return -1;
        }
        return 0;
    }
    fprintf(stderr, "mknod %s failed (%s); binding host device instead\n",
            path, strerror(errno));
    return bind_host_device(node->name, path);
}

/*
 * A private tmpfs on <root>/dev holds the device nodes. This runs BEFORE the
 * pivot because the bind-mount fallback needs the host's /dev.
 */
static int setup_dev(const char *rootfs)
{
    char dev_dir[PATH_MAX];
    size_t i;

    if (join_path(dev_dir, sizeof(dev_dir), rootfs, "dev") == -1)
        return -1;
    if (ensure_dir(dev_dir, 0755) == -1)
        return -1;
    if (mount("tmpfs", dev_dir, "tmpfs", MS_NOSUID | MS_NOEXEC,
              "mode=755,size=64k") == -1) {
        perror("mount(tmpfs /dev)");
        return -1;
    }
    for (i = 0; i < sizeof(DEV_NODES) / sizeof(DEV_NODES[0]); i++) {
        if (make_device(dev_dir, &DEV_NODES[i]) == -1)
            return -1;
    }
    return 0;
}

/*
 * Swap the namespace root to `rootfs`, parking the old root at
 * <rootfs>/.pivot_old, then unmount and delete it.
 * MNT_DETACH = lazy unmount, so it succeeds even if something still uses it.
 */
static int do_pivot_root(const char *rootfs)
{
    char old_root[PATH_MAX];

    if (join_path(old_root, sizeof(old_root), rootfs, OLD_ROOT_NAME) == -1)
        return -1;
    if (ensure_dir(old_root, 0700) == -1)
        return -1;
    /* glibc has no pivot_root() wrapper, so call the syscall directly. */
    if (syscall(SYS_pivot_root, rootfs, old_root) == -1) {
        perror("pivot_root");
        return -1;
    }
    if (chdir("/") == -1) {
        perror("chdir(/)");
        return -1;
    }
    if (umount2("/" OLD_ROOT_NAME, MNT_DETACH) == -1) {
        perror("umount2(old root)");
        return -1;
    }
    if (rmdir("/" OLD_ROOT_NAME) == -1) {
        perror("rmdir(old root)");
        return -1;
    }
    return 0;
}

/*
 * procfs is generated by the kernel for the *mounting* process's PID
 * namespace, so mounting it inside the container shows only container PIDs.
 */
static int mount_proc(void)
{
    if (ensure_dir("/proc", 0555) == -1)
        return -1;
    if (mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC,
              NULL) == -1) {
        perror("mount(proc)");
        return -1;
    }
    return 0;
}

static int mount_tmp(void)
{
    if (ensure_dir("/tmp", 01777) == -1)
        return -1;
    if (mount("tmpfs", "/tmp", "tmpfs", MS_NOSUID | MS_NODEV,
              "mode=1777") == -1) {
        perror("mount(tmpfs /tmp)");
        return -1;
    }
    return 0;
}

int rootfs_setup(const char *rootfs)
{
    if (make_mounts_private() == -1)
        return -1;
    if (bind_rootfs(rootfs) == -1)
        return -1;
    if (setup_dev(rootfs) == -1)
        return -1;
    if (do_pivot_root(rootfs) == -1)
        return -1;
    if (mount_proc() == -1)
        return -1;
    return mount_tmp();
}
