# src/container.c

## Purpose
Creates the container process with `clone()` in new PID, UTS and mount
namespaces, runs the child setup sequence, and waits for the child.

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `static void *alloc_stack(void **top)` | out-pointer for stack top | mapping base, or NULL | `mmap` of 1 MiB (`MAP_PRIVATE\|MAP_ANONYMOUS\|MAP_STACK`); `perror` on failure. `*top` = base + size because the stack grows down. |
| `static int set_hostname(const char *name)` | hostname | 0 / -1 | `sethostname`; `perror`. Only affects the child's UTS namespace. |
| `static int child_main(void *arg)` | `struct container_config *` | child exit code | Runs as PID 1 of the new PID ns: `set_hostname` → `rootfs_setup` → `chdir("/")` → `setenv` PATH/HOME → `execvp`. Returns 1 on setup failure, 127 if exec fails. On success it never returns. |
| `static int wait_for_child(pid_t pid)` | child pid | exit code; 128+sig if killed by signal; -1 on error | `waitpid`, retries on `EINTR` |
| `int container_run(const struct container_config *cfg)` | config | child's exit status, or -1 | Allocates stack, `clone`, waits, `munmap`. |

## System calls used and why
- `mmap` / `munmap` – `clone()` needs a caller-supplied stack for the child.
- `clone(CLONE_NEWPID|CLONE_NEWUTS|CLONE_NEWNS|SIGCHLD)` – like `fork`, but the child gets new PID, UTS and mount namespaces. `SIGCHLD` lets the parent `waitpid` normally.
- `sethostname` – set hostname in the new UTS namespace.
- `chdir`, `setenv`, `execvp` – start the user's program in the new root with a sane PATH.
- `waitpid` – collect the exit status (avoids a zombie).

## OS concept
Namespaces (per-process views of kernel resources) and process creation.
The first process in a new PID namespace is PID 1 there.

## How to test
`sudo tests/phase1_test.sh`, or manually:
```sh
sudo ./minibox run --rootfs rootfs --hostname mybox -- /bin/sh
/ # hostname      # mybox
/ # echo $$       # 1
sudo ./minibox run --rootfs rootfs -- sh -c 'exit 42'; echo $?    # 42
sudo ./minibox run --rootfs rootfs -- nosuchcmd; echo $?          # error, 127
```
