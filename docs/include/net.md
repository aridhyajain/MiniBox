# include/net.h

## Purpose
Public interface of the network module (used by `src/container.c`, `src/state.c` and `src/commands.c`, implemented in `src/net.c`).

## Declarations
- `enum net_mode { NET_NONE, NET_VETH }` – value of `--net`.
- Constants: `NET_IFNAME_LEN` (16), `NET_CONTAINER_IF` (`"eth0"`), `NET_PREFIX` (24), `NET_ADDR_LEN` (16), `NET_MAX_INDEX` (255). The fixed 10.0.0.x address constants were removed.
- `int net_loopback_up(void)`, `int net_set_ipv4(ifname, addr, prefix)` – child side; return 0 / -1, errors via `perror`.
- `void net_subnet_addrs(index, host, cont)` – `10.0.<index>.1` / `.2`.
- `void net_veth_names(pid, host, cont)`, `int net_veth_setup(pid, index)`, `void net_veth_cleanup(pid)` – host side.

Full signatures, errors and side effects: `docs/src/net.md`.

## System calls used
None (declarations only).

## OS concept
Network namespace configuration.

## How to test
`make`, then `sudo tests/phase2_test.sh`.
