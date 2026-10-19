/* net.h - network setup for the container's network namespace. */
#ifndef MINIBOX_NET_H
#define MINIBOX_NET_H

#include <sys/types.h>

enum net_mode {
    NET_NONE,    /* only the loopback interface */
    NET_VETH     /* loopback plus a veth pair to the host */
};

#define NET_IFNAME_LEN      16            /* same as IFNAMSIZ */
#define NET_CONTAINER_IF    "eth0"        /* name of the veth end inside */
#define NET_PREFIX          24
#define NET_ADDR_LEN        16            /* "255.255.255.255" + NUL */
#define NET_MAX_INDEX       255           /* subnets 10.0.0.0/24 .. 10.0.255.0/24 */

/*
 * Addresses of subnet number `index` (0..NET_MAX_INDEX): host 10.0.<index>.1,
 * container 10.0.<index>.2, both /NET_PREFIX. Every veth container gets its
 * own index so several can run at once without address clashes.
 */
void net_subnet_addrs(int index, char host[NET_ADDR_LEN], char cont[NET_ADDR_LEN]);

/* ---- child side: runs inside the new network namespace (ioctl, no `ip`) ---- */

/* Bring up `lo`. Returns 0 on success, -1 on error (reported with perror). */
int net_loopback_up(void);

/*
 * Assign addr/prefix (e.g. "10.0.0.2", 24) to `ifname` and bring it up.
 * Returns 0 on success, -1 on error (reported with perror).
 */
int net_set_ipv4(const char *ifname, const char *addr, int prefix);

/* ---- host side: runs in the parent (uses the `ip` command via fork+exec) ---- */

/* Fill the unique veth names vethh<pid> (host) and vethc<pid> (container). */
void net_veth_names(pid_t pid, char host[NET_IFNAME_LEN], char cont[NET_IFNAME_LEN]);

/*
 * Create the veth pair, move the peer into the netns of process `pid`
 * (renamed to eth0), give the host end 10.0.<index>.1/24 and bring it up.
 * Returns 0 on success, -1 on error (message printed). On error the caller
 * should still call net_veth_cleanup().
 */
int net_veth_setup(pid_t pid, int index);

/* Delete the host-side veth for `pid` if it still exists. Never fails hard. */
void net_veth_cleanup(pid_t pid);

#endif /* MINIBOX_NET_H */
