/*
 * net.c - network namespace setup: loopback, veth pair, IPv4 addresses.
 *
 * OS concept: a network namespace has its own interfaces, routes and ports.
 * A new one contains only a DOWN loopback device. A veth pair is a virtual
 * cable: two interfaces where whatever enters one comes out of the other, and
 * the two ends may live in different namespaces.
 *
 * Tradeoff: the child configures its own interfaces with socket ioctls (no
 * external program). The parent creates the veth pair with the `ip` command
 * (fork + execvp, exit status checked) because the real alternative is
 * hand-written rtnetlink messages, which are long and hard to explain.
 */
#define _GNU_SOURCE
#include "net.h"

#include <arpa/inet.h>
#include <errno.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

/* ------------------------- child side (ioctl) ------------------------- */

/* Any socket works as a handle for interface ioctls; UDP/IPv4 is simplest. */
static int open_ioctl_socket(void)
{
    int fd = socket(AF_INET, SOCK_DGRAM, 0);

    if (fd == -1)
        perror("socket(AF_INET)");
    return fd;
}

static void init_ifreq(struct ifreq *ifr, const char *ifname)
{
    memset(ifr, 0, sizeof(*ifr));
    snprintf(ifr->ifr_name, sizeof(ifr->ifr_name), "%s", ifname);
}

/* Set the IFF_UP flag: read flags, OR in IFF_UP, write them back. */
static int if_up_fd(int fd, const char *ifname)
{
    struct ifreq ifr;

    init_ifreq(&ifr, ifname);
    if (ioctl(fd, SIOCGIFFLAGS, &ifr) == -1) {
        perror("ioctl(SIOCGIFFLAGS)");
        return -1;
    }
    ifr.ifr_flags |= IFF_UP;
    if (ioctl(fd, SIOCSIFFLAGS, &ifr) == -1) {
        perror("ioctl(SIOCSIFFLAGS)");
        return -1;
    }
    return 0;
}

static void fill_sockaddr_in(struct sockaddr *sa, uint32_t addr_be)
{
    struct sockaddr_in sin;

    memset(&sin, 0, sizeof(sin));
    sin.sin_family = AF_INET;
    sin.sin_addr.s_addr = addr_be;
    memcpy(sa, &sin, sizeof(sin));
}

/* "/24" -> 255.255.255.0 in network byte order. */
static uint32_t prefix_to_netmask(int prefix)
{
    if (prefix <= 0)
        return 0;
    return htonl(0xFFFFFFFFu << (32 - prefix));
}

int net_loopback_up(void)
{
    int fd = open_ioctl_socket();
    int ret;

    if (fd == -1)
        return -1;
    ret = if_up_fd(fd, "lo");
    close(fd);
    return ret;
}

int net_set_ipv4(const char *ifname, const char *addr, int prefix)
{
    struct in_addr ip;
    struct ifreq ifr;
    int fd, ret = -1;

    if (prefix < 0 || prefix > 32 || inet_pton(AF_INET, addr, &ip) != 1) {
        fprintf(stderr, "net: bad address %s/%d\n", addr, prefix);
        return -1;
    }
    fd = open_ioctl_socket();
    if (fd == -1)
        return -1;

    init_ifreq(&ifr, ifname);
    fill_sockaddr_in(&ifr.ifr_addr, ip.s_addr);
    if (ioctl(fd, SIOCSIFADDR, &ifr) == -1) {
        perror("ioctl(SIOCSIFADDR)");
        goto out;
    }
    fill_sockaddr_in(&ifr.ifr_netmask, prefix_to_netmask(prefix));
    if (ioctl(fd, SIOCSIFNETMASK, &ifr) == -1) {
        perror("ioctl(SIOCSIFNETMASK)");
        goto out;
    }
    ret = if_up_fd(fd, ifname);   /* bringing it up also adds the subnet route */
out:
    close(fd);
    return ret;
}

/* ------------------------- host side (ip command) ------------------------- */

/*
 * Run `ip` with argv (argv[0] is "ip", NULL-terminated) and wait for it.
 * Returns 0 only if it exited with status 0. Never uses system().
 */
static int run_ip(char *const argv[])
{
    int status;
    pid_t pid = fork();

    if (pid == -1) {
        perror("fork(ip)");
        return -1;
    }
    if (pid == 0) {
        execvp("ip", argv);
        perror("exec ip");
        _exit(127);
    }
    while (waitpid(pid, &status, 0) == -1) {
        if (errno != EINTR) {
            perror("waitpid(ip)");
            return -1;
        }
    }
    return (WIFEXITED(status) && WEXITSTATUS(status) == 0) ? 0 : -1;
}

static void print_failed(char *const argv[])
{
    int i;

    fprintf(stderr, "minibox: command failed:");
    for (i = 0; argv[i] != NULL; i++)
        fprintf(stderr, " %s", argv[i]);
    fputc('\n', stderr);
}

/* run_ip plus an error message that shows the command line. */
static int run_ip_checked(char *const argv[])
{
    if (run_ip(argv) == -1) {
        print_failed(argv);
        return -1;
    }
    return 0;
}

void net_veth_names(pid_t pid, char host[NET_IFNAME_LEN], char cont[NET_IFNAME_LEN])
{
    /* pid is unique among live processes, so the names never clash. */
    snprintf(host, NET_IFNAME_LEN, "vethh%d", (int)pid);
    snprintf(cont, NET_IFNAME_LEN, "vethc%d", (int)pid);
}

void net_subnet_addrs(int index, char host[NET_ADDR_LEN], char cont[NET_ADDR_LEN])
{
    snprintf(host, NET_ADDR_LEN, "10.0.%d.1", index);
    snprintf(cont, NET_ADDR_LEN, "10.0.%d.2", index);
}

int net_veth_setup(pid_t pid, int index)
{
    char host[NET_IFNAME_LEN], cont[NET_IFNAME_LEN], pidstr[16];
    char host_addr[NET_ADDR_LEN], cont_addr[NET_ADDR_LEN], host_cidr[NET_ADDR_LEN + 4];
    char *add[]  = { "ip", "link", "add", host, "type", "veth", "peer", "name", cont, NULL };
    /* moving to a netns identified by PID uses /proc/<pid>/ns/net */
    char *move[] = { "ip", "link", "set", cont, "netns", pidstr, "name", NET_CONTAINER_IF, NULL };
    char *addr[] = { "ip", "addr", "add", host_cidr, "dev", host, NULL };
    char *up[]   = { "ip", "link", "set", host, "up", NULL };

    net_veth_names(pid, host, cont);
    net_subnet_addrs(index, host_addr, cont_addr);
    snprintf(host_cidr, sizeof(host_cidr), "%s/%d", host_addr, NET_PREFIX);
    snprintf(pidstr, sizeof(pidstr), "%d", (int)pid);
    if (run_ip_checked(add) == -1 || run_ip_checked(move) == -1)
        return -1;
    if (run_ip_checked(addr) == -1 || run_ip_checked(up) == -1)
        return -1;
    return 0;
}

/*
 * When the container's netns is destroyed the kernel deletes the peer, and a
 * veth pair dies together, so the host end normally disappears by itself
 * (a little after the process exits). Deleting it here covers the case where
 * setup failed before the peer was moved.
 */
void net_veth_cleanup(pid_t pid)
{
    char host[NET_IFNAME_LEN], cont[NET_IFNAME_LEN];
    char *del[] = { "ip", "link", "del", host, NULL };

    net_veth_names(pid, host, cont);
    if (if_nametoindex(host) == 0)
        return;
    if (run_ip(del) == -1 && if_nametoindex(host) != 0)
        print_failed(del);
}
