"""Which addresses count as internal (spec §8.2). Everything else is external."""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network

from nettriage.domain.flows import IPAddress

INTERNAL_NETWORKS: tuple[IPv4Network | IPv6Network, ...] = tuple(
    ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "fc00::/7",
        "fe80::/10",
    )
)


@lru_cache(maxsize=65_536)
def is_internal(address: IPAddress) -> bool:
    """RFC 1918, 100.64.0.0/10 (carrier-grade NAT), fc00::/7 (unique local) or fe80::/10
    (link-local). Configurable in a later milestone."""
    return any(
        address.version == network.version and address in network for network in INTERNAL_NETWORKS
    )
