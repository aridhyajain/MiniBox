# src/rootfs.c

## Purpose
Runs inside the container's new mount namespace and replaces the root
filesystem with the Alpine directory using `pivot_root`, mounting `/dev`, `/proc` and `/tmp`.

## Order of operations (`rootfs_setup`)
1. `make_mounts_private` 2. `bind_rootfs` 3. `setup_dev` (needs host `/dev`, so before the pivot)
4. `do_pivot_root` 5. `mount_proc` 6. `mount_tmp`

## Functions (all static except `rootfs_setup`)
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int rootfs_setup(const char *rootfs)` | absolute rootfs path | 0 / -1 | Runs steps above; stops at first failure (error already printed). |
| `ensure_dir(path, mode)` | path, mode | 0 / -1 | `mkdir`, `EEXIST` is fine; `perror` otherwise |
| `join_path(buf, size, dir, name)` | buffer | 0 / -1 | `snprintf` with truncation check |
| `make_mounts_private()` | – | 0 / -1 | `mount("none","/",NULL,MS_REC\|MS_PRIVATE)`; stops mount events leaking to/from the host |
| `bind_rootfs(rootfs)` | path | 0 / -1 | bind-mounts the dir onto itself so it's a mount point (pivot_root requirement) |
| `setup_dev(rootfs)` | path | 0 / -1 | creates `<rootfs>/dev`, mounts tmpfs (64k), creates 5 nodes |
| `make_device(dev_dir, node)` | dir, node table entry | 0 / -1 | `mknod` char device (`null` 1:3, `zero` 1:5, `random` 1:8, `urandom` 1:9, `tty` 5:0), `chmod 0666`; if `mknod` fails, prints a note and uses `bind_host_device` |
| `bind_host_device(name, target)` | – | 0 / -1 | creates empty file, bind-mounts host `/dev/<name>` on it |
| `do_pivot_root(rootfs)` | path | 0 / -1 | mkdir `<rootfs>/.pivot_old`; `pivot_root(rootfs, old)`; `chdir("/")`; `umount2("/.pivot_old", MNT_DETACH)`; `rmdir` |
| `mount_proc()` | – | 0 / -1 | mounts procfs on `/proc` (nosuid,nodev,noexec) |
| `mount_tmp()` | – | 0 / -1 | tmpfs on `/tmp`, mode 1777 |

Side effects: the rootfs directory itself is used in place; changes made inside
(files outside `/dev`, `/proc`, `/tmp`) persist in `rootfs/`. Mounts vanish when the namespace dies.

## System calls used and why
`mount` (private/bind/tmpfs/proc), `mknod`, `chmod`, `open`/`close` (bind-mount target file),
`mkdir`/`rmdir`, `syscall(SYS_pivot_root)` (glibc has no wrapper), `chdir`, `umount2(MNT_DETACH)` (lazy unmount of old root).

## OS concept
Mount namespaces and the mount table; mount propagation (shared vs private);
`pivot_root` vs `chroot` (chroot only changes the path-resolution root and is escapable; pivot_root swaps the namespace root and lets us unmount the host filesystem).

## How to test
```sh
sudo ./minibox run --rootfs rootfs -- sh -c 'ls /; cat /etc/os-release; ls -l /dev; mount; ls /home'
sudo tests/phase1_test.sh
```
Expect Alpine files, 5 device nodes, `proc`/`tmpfs` mounts, and no host mounts or home dirs.
Also verify that the host `mount` output is unchanged after the container exits.
