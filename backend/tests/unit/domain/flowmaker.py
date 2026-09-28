"""Build `NetworkFlow` records for detector tests without writing log lines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from itertools import count

from nettriage.domain.flows import TCP, Action, FlowSource, NetworkFlow

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SOURCE = FlowSource(provider="aws_vpc", interface_id="eni-test")
_line_numbers = count(1)


def make_flow(
    src: str,
    dst: str,
    dst_port: int,
    *,
    at: float = 0.0,
    duration: float = 1.0,
    src_port: int = 40_000,
    protocol: int = TCP,
    packets: int = 1,
    bytes: int = 60,
    action: Action = "REJECT",
    tcp_flags: int | None = None,
) -> NetworkFlow:
    """`at` and `duration` are seconds after 12:00:00 UTC on 2026-09-01."""
    start = T0 + timedelta(seconds=at)
    return NetworkFlow(
        src_ip=ip_address(src),
        dst_ip=ip_address(dst),
        src_port=src_port,
        dst_port=dst_port,
        protocol=protocol,
        packets=packets,
        bytes=bytes,
        start=start,
        end=start + timedelta(seconds=duration),
        action=action,
        source=SOURCE,
        line_no=next(_line_numbers),
        tcp_flags=tcp_flags,
    )
