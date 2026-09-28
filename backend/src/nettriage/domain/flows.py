"""The provider-neutral network flow record that every parser produces (spec §8.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Literal

type IPAddress = IPv4Address | IPv6Address
type Action = Literal["ACCEPT", "REJECT"]
type Direction = Literal["ingress", "egress"]

ICMP = 1
TCP = 6
UDP = 17
PROTOCOL_NAMES = {ICMP: "ICMP", TCP: "TCP", UDP: "UDP"}


def protocol_name(protocol: int) -> str:
    """`TCP`, `UDP` or `ICMP`, otherwise the IANA number as text."""
    return PROTOCOL_NAMES.get(protocol, str(protocol))


@dataclass(frozen=True, slots=True)
class FlowSource:
    """Where a flow was recorded. M3's Azure parser fills the same record."""

    provider: str
    interface_id: str | None = None
    vpc_id: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkFlow:
    """One flow record. `start` and `end` are timezone-aware UTC datetimes; `line_no` is the
    1-based line in the uploaded file, kept so evidence can point back to it."""

    src_ip: IPAddress
    dst_ip: IPAddress
    src_port: int
    dst_port: int
    protocol: int
    packets: int
    bytes: int
    start: datetime
    end: datetime
    action: Action
    source: FlowSource
    line_no: int
    direction: Direction | None = None
    tcp_flags: int | None = None
