"""One VPC Flow Logs record to a `NetworkFlow` (spec §8.1): field layout, nulls, validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from ipaddress import ip_address
from typing import cast

from nettriage.domain.flows import Action, Direction, FlowSource, IPAddress, NetworkFlow

PROVIDER = "aws_vpc"
DEFAULT_V2_FIELDS: tuple[str, ...] = (
    "version",
    "account-id",
    "interface-id",
    "srcaddr",
    "dstaddr",
    "srcport",
    "dstport",
    "protocol",
    "packets",
    "bytes",
    "start",
    "end",
    "action",
    "log-status",
)
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"srcaddr", "dstaddr", "srcport", "dstport", "protocol"}
    | {"packets", "bytes", "start", "end", "action"}
)
SKIPPED_STATUSES: frozenset[str] = frozenset({"NODATA", "SKIPDATA"})
NULL = "-"
MAX_PORT = 65_535
MAX_PROTOCOL = 255
# 9999-12-31T23:59:59Z: the largest Unix time a datetime can hold.
MAX_EPOCH_SECONDS = 253_402_300_799
_FIELD_NAME = re.compile(r"[a-z][a-z0-9-]*")


class RecordError(ValueError):
    """A record that isn't a valid flow. `reason` is a short, stable code for the UI."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SkippedRecord(Exception):
    """A NODATA or SKIPDATA record: valid, but it carries no flow."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class Layout:
    """The order of the fields in each record, and whether a header row declared it."""

    fields: tuple[str, ...]
    has_header: bool

    def position(self, name: str) -> int | None:
        return _positions(self.fields).get(name)


@lru_cache(maxsize=16)
def _positions(fields: tuple[str, ...]) -> dict[str, int]:
    return {name: index for index, name in enumerate(fields)}


def layout_for(first_line: str) -> Layout:
    """A first line whose every token looks like a field name is a header (as in S3-delivered
    files); it must name every required field. Anything else is a record in AWS's default
    version 2 format."""
    tokens = tuple(first_line.split())
    if tokens and all(_FIELD_NAME.fullmatch(token) for token in tokens):
        missing = sorted(REQUIRED_FIELDS - set(tokens))
        if missing:
            raise RecordError("header lacks required fields: " + ", ".join(missing))
        return Layout(tokens, has_header=True)
    return Layout(DEFAULT_V2_FIELDS, has_header=False)


def parse_record(line: str, layout: Layout, line_no: int) -> NetworkFlow:
    """Raises `SkippedRecord` for NODATA/SKIPDATA and `RecordError` for anything invalid.
    Unknown fields are ignored; optional fields that don't parse are treated as missing."""
    values = line.split()
    if len(values) != len(layout.fields):
        raise RecordError("field_count")

    def value(name: str) -> str | None:
        index = layout.position(name)
        if index is None or values[index] == NULL:
            return None
        return values[index]

    def required(name: str) -> str:
        found = value(name)
        if found is None:
            raise RecordError("missing_field")
        return found

    status = value("log-status")
    if status in SKIPPED_STATUSES:
        raise SkippedRecord(status)

    src_ip = _address(required("srcaddr"))
    dst_ip = _address(required("dstaddr"))
    start = _time(required("start"))
    end = _time(required("end"))
    if start > end:
        raise RecordError("start_after_end")
    action = required("action")
    if action not in ("ACCEPT", "REJECT"):
        raise RecordError("bad_action")
    return NetworkFlow(
        src_ip=_optional_address(value("pkt-srcaddr")) or src_ip,
        dst_ip=_optional_address(value("pkt-dstaddr")) or dst_ip,
        src_port=_bounded(required("srcport"), MAX_PORT, "bad_port"),
        dst_port=_bounded(required("dstport"), MAX_PORT, "bad_port"),
        protocol=_bounded(required("protocol"), MAX_PROTOCOL, "bad_protocol"),
        packets=_count(required("packets")),
        bytes=_count(required("bytes")),
        start=start,
        end=end,
        action=cast(Action, action),
        source=_source(value("interface-id"), value("vpc-id")),
        line_no=line_no,
        direction=_direction(value("flow-direction")),
        tcp_flags=_optional_count(value("tcp-flags")),
    )


def _address(text: str) -> IPAddress:
    try:
        return ip_address(text)
    except ValueError:
        raise RecordError("bad_address") from None


def _optional_address(text: str | None) -> IPAddress | None:
    try:
        return ip_address(text) if text is not None else None
    except ValueError:
        return None


def _is_number(text: str) -> bool:
    # isdigit() alone accepts non-ASCII digits such as "٣"; int() accepts "+5" and "1_000".
    return text.isascii() and text.isdigit()


def _bounded(text: str, maximum: int, reason: str) -> int:
    if not _is_number(text) or int(text) > maximum:
        raise RecordError(reason)
    return int(text)


def _count(text: str) -> int:
    if not _is_number(text):
        raise RecordError("bad_count")
    return int(text)


def _optional_count(text: str | None) -> int | None:
    return int(text) if text is not None and _is_number(text) else None


def _time(text: str) -> datetime:
    if not _is_number(text) or int(text) > MAX_EPOCH_SECONDS:
        raise RecordError("bad_time")
    return datetime.fromtimestamp(int(text), UTC)


def _direction(text: str | None) -> Direction | None:
    return cast(Direction, text) if text in ("ingress", "egress") else None


@lru_cache(maxsize=4_096)
def _source(interface_id: str | None, vpc_id: str | None) -> FlowSource:
    return FlowSource(provider=PROVIDER, interface_id=interface_id, vpc_id=vpc_id)
