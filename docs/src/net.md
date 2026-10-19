# src/net.c

## Purpose
Network setup for the container's network namespace: bring up loopback, assign an IPv4 address,
and create/clean up the veth pair that links the container to the host.
Two halves: **child side** (runs inside the new netns, uses socket ioctls only) and
**host side** (runs in the parent, uses the `ip` command).

## Functions
| Function | Inputs | Returns | Errors / side effects |
|----------|--------|---------|-----------------------|
| `int net_loopback_up(void)` | – | 0 / -1 | brings `lo` up via `SIOCGIFFLAGS`/`SIOCSIFFLAGS`; `perror` on error |
| `int net_set_ipv4(const char *ifname, const char *addr, int prefix)` | interface, dotted IPv4, prefix 0–32 | 0 / -1 | `SIOCSIFADDR`, `SIOCSIFNETMASK`, then brings the interface up (the kernel then adds the connected subnet route). Rejects a bad address/prefix. |
| `void net_veth_names(pid_t pid, char host[16], char cont[16])` | pid | – | writes `vethh<pid>` / `vethc<pid>` |
| `int net_veth_setup(pid_t pid)` | container PID (as seen from the host) | 0 / -1 | runs, in order: `ip link add vethh<pid> type veth peer name vethc<pid>`; `ip link set vethc<pid> netns <pid> name eth0`; `ip addr add 10.0.0.1/24 dev vethh<pid>`; `ip link set vethh<pid> up`. Prints the failing command on error. |
| `void net_veth_cleanup(pid_t pid)` | pid | – | if `vethh<pid>` still exists, `ip link del` it; quiet if it vanished meanwhile |
| static `open_ioctl_socket`, `init_ifreq`, `if_up_fd`, `fill_sockaddr_in`, `prefix_to_netmask` | – | – | small ioctl helpers |
| static `run_ip(argv)` | `ip` argument vector | 0 only if `ip` exited with status 0 | `fork` + `execvp("ip")` + `waitpid`; never `system()` |
| static `run_ip_checked(argv)`, `print_failed(argv)` | – | – | `run_ip` plus an error message with the command line |

Constants (in `include/net.h`): container end is `eth0` = **10.0.0.2/24**, host end **10.0.0.1/24**.

## System calls used and why
- `socket(AF_INET, SOCK_DGRAM)` + `ioctl(SIOCGIFFLAGS / SIOCSIFFLAGS / SIOCSIFADDR / SIOCSIFNETMASK)` – the classic interface to configure an interface without any helper program. The socket is only a handle; it needs no traffic.
- `fork`, `execvp`, `waitpid` – run `ip` for veth creation (parent).
- `if_nametoindex` – check whether the host veth still exists.

## Tradeoff: `ip` in the parent vs. netlink
Creating a veth pair is done in the kernel via **rtnetlink** messages (`RTM_NEWLINK` with `IFLA_INFO_KIND="veth"`,
peer attributes, `IFLA_NET_NS_PID` to move the peer). Writing that by hand is ~200 lines of attribute packing, which is hard to
explain in a viva. Running `ip` (which sends exactly those messages) keeps the code short and readable, at the cost of
depending on iproute2 on the host and spawning 4 processes. Loopback and the container-side address do **not** use `ip`; they use ioctls
in the child, which also avoids needing `nsenter`/named namespaces.

## Limitations
- Fixed subnet 10.0.0.0/24: interface *names* are unique per container, but two simultaneous `--net veth` containers would both use 10.0.0.2 (and host 10.0.0.1 twice). Use one at a time.
- No NAT/routing to the internet.

## OS concept
Network namespaces (own interfaces, routes, ports), virtual Ethernet pairs, interface configuration via ioctl.

## How to test
```sh
sudo tests/phase2_test.sh
sudo ./minibox run --rootfs rootfs --net veth -- sh -c 'ip addr; ping -c1 10.0.0.1'
ip link | grep vethh        # on the host after exit: nothing
```
