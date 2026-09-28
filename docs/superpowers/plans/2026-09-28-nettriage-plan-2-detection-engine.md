# NetTriage Plan 2: Detection Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an uploaded VPC Flow Logs file into ranked, fingerprinted findings from three detectors (port scan, SSH/RDP brute force, unusual outbound volume). A seeded synthetic suite measures their precision and recall, and CI publishes the result.

**Architecture:** Pure domain code in `backend/src/nettriage/domain/` with no cloud, database or HTTP imports.
- A streaming parser turns plain or gzip bytes into provider-neutral `NetworkFlow` records, within strict limits.
- Each detector is a function from flows to `Finding` objects. All three share sliding 5-minute windows, fingerprints and evidence sampling.
- `detect()` runs them all, then ranks and caps the results.
- `tools/scenarios` generates labeled flow logs and scores the detectors against the labels.

Persistence, S3, queues and the `analyze` worker come in Plan 4, and they call this code unchanged.

**Tech Stack:** Python 3.14 (standard library only in `domain/`), pytest, Hypothesis, pytest-cov, Ruff, mypy (strict) · GitHub Actions for the report.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements §8.1, §8.2 (including "Quality measurement"), the finding and evidence shapes of §5, the per-upload finding cap of §5, and the parser and coverage parts of §11.4.

**Plan series:** This is Plan 2 of 7 for Milestone 1: (1) walking skeleton, (2) **detection engine**, (3) data, identity and access, (4) upload pipeline, (5) AI triage, (6) frontend, (7) operations and launch. Plan 1 (with Plan 1b) is merged and deployed.

**Branch:** `plan-2/detection-engine`, from `main` at `fad4b31` or later.

## Global Constraints

- **Pure domain code.** Python **3.14**. Everything under `backend/src/nettriage/domain/` uses only the standard library, with no AWS, database, HTTP or framework imports (§11.1).
- **Checks.** mypy `--strict` and Ruff, both configured in `backend/pyproject.toml`, pass on `src` and `tests`. Work test-first (TDD).
- **Commands on this Windows machine.** Run Python tools as modules, because host policy blocks some uv launchers:
  - backend tests: `cd backend && uv run python -m pytest …`;
  - tools tests: `uv run --project backend python -m pytest tools/tests …`;
  - `just lint`, `just test` and `just tools-test` wrap these.
- **Sizes are binary.** 1 KB = 1,024 bytes and 1 MB = 1,048,576 bytes. This applies to the parser limits, the 100 KB login-success size, and the 50 MB and 500 MB outbound thresholds.
- **Parser (§8.1), exactly:**
  - Input is plain text or gzip. Gzip is detected from the first bytes `1f 8b`, never from the file name.
  - A header line defines the field order. Without one, AWS's default version 2 format (14 fields) is assumed. Unknown fields are ignored.
  - Required fields: `srcaddr`, `dstaddr`, `srcport`, `dstport`, `protocol`, `packets`, `bytes`, `start`, `end`, `action`.
  - Used when present: `tcp-flags`, `flow-direction`, `pkt-srcaddr`, `pkt-dstaddr`, `vpc-id`, `interface-id`, `log-status`.
  - `-` means null. IPs must parse, ports must be 0–65535, protocol 0–255, counts must not be negative, `start ≤ end`, and `action` must be ACCEPT or REJECT.
  - NODATA and SKIPDATA records are skipped and counted.
  - Limits: 250 MB decompressed, 2,000,000 rows, 4 KB per line. Hitting one fails with `limit_exceeded`.
  - If more than 5% of records are invalid, the file fails with `not_a_flow_log`.
  - At most 20 rejected samples are kept, each with its line number, reason and first 120 characters.
- **Internal addresses (§8.2):** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `fc00::/7` and `fe80::/10`. Everything else is external.
- **Detectors (§8.2), exactly:**
  - Windows are computed on flow start times. A 5-minute window holds the flows that start less than 300 seconds after its first flow.
  - `port_scan` v1: in any 5-minute window, one source reaches **≥ 100** distinct destination ports on one host (vertical), or **≥ 50** distinct hosts on one port (horizontal), with **≥ 80%** of the window's flows REJECT or at most **3** packets.
    - Severity: an internal source is high. An external source is medium, or high at **1,000+** ports or hosts.
    - Techniques: external T1595 and T1595.001; internal T1046.
  - `remote_access_bruteforce` v1: in any 5-minute window, one source makes **≥ 30** flows to TCP 22 or 3389 on one host, each with at most **20** packets. The spraying variant: one source reaches **≥ 10** hosts on 22 or 3389.
    - Severity: medium. It's high ("possible successful login") when a later flow from the same source to the same host and port, within **30 minutes**, carries **≥ 100 KB** or lasts **≥ 5 minutes**.
    - Techniques: T1110, T1110.001 and T1110.003, plus T1021.004 (SSH) or T1021.001 (RDP) when a login may have succeeded.
  - `outbound_volume` v1: an internal source sends **≥ 50 MB** to one external destination within the file, and its robust z-score (median and MAD of the per-host maximum outbound volumes) is **≥ 5**. With fewer than **5** internal hosts, or MAD = 0, only the absolute threshold applies.
    - Severity: medium. It's high at **≥ 500 MB**, or when the destination port is neither 80 nor 443.
    - Techniques: T1048, T1041 and T1567.
- **Findings (§8.2, §5):**
  - Fingerprint: `sha256(detector_id, version, key entities, window start rounded down to 5 minutes)`.
  - Evidence: at most 50 flows per finding, made of the first 10, the last 10, and 30 sampled evenly between them.
  - At most **50 findings per upload**, highest severity first. The rest are counted as truncated.
- **Quality (§8.2):** on the generated suite, each detector must reach precision **≥ 0.90** and recall **≥ 0.90**. CI fails below that and publishes the report.
- **Coverage (§11.4):** at least 85% line coverage of `nettriage.domain`.
- **Out of scope:** persistence, S3 streaming, queues, the `analyze` Lambda, API endpoints, AI, frontend and metrics. They belong to Plans 3–7. Domain code never logs anything, so raw upload lines can't reach logs.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes where the spec is silent

The owner reviews these, and the code implements them exactly:
1. **Windows slide, and qualifying windows merge into episodes.** Every window that meets a rule qualifies. Windows that share flows merge into one episode, and each episode is one finding. So an hour-long scan is one finding, not sixty, and an attack across a 5-minute boundary is still caught.
2. **Packet-level addresses win.** When a record has `pkt-srcaddr` or `pkt-dstaddr` (the original packet addresses, for example behind a NAT gateway), they replace `srcaddr` and `dstaddr`. `srcaddr` and `dstaddr` must still be valid.
3. **Port scans count TCP and UDP only**, each protocol separately, because ICMP has no ports.
4. **The robust z-score** is `(pair total − median) / (1.4826 × MAD)`, where the median and MAD are taken over each internal host's largest outbound volume to any one external destination. Only ACCEPT flows count. When a pair's traffic uses several ports, the "destination port" is the one that carried the most bytes.
5. **"Later flow" for a possible login** means one that starts between the first attempt and 30 minutes after the last attempt, to the same host and port. For spraying, that's any sprayed host.
6. **Files with no data or no valid lines.**
   - An empty or header-only file is `not_a_flow_log`.
   - A file of only NODATA/SKIPDATA records is valid and has no flows.
   - An over-long line before any valid record is `not_a_flow_log`, so a binary file reads as "not a flow log", not as a limit.
   - Optional fields that don't parse are treated as missing.
7. **One finding per fingerprint.** Duplicates are dropped when findings are ranked.
8. **Scans across many ports and many hosts** give one finding per host and one per port, capped at 50 per upload. That's accepted for Milestone 1.

## Review Focus

1. **Files that aren't flow logs**, such as a CSV or JSON export, a PNG, an empty file or a header-only file. They must fail with `not_a_flow_log` and a readable detail: never a crash, and never `limit_exceeded` for a binary file. Test: Task 3 `test_files_that_are_not_flow_logs_say_so`.
2. **Real-world text quirks**: CRLF line endings, blank lines, no final newline, a header with unknown fields in a custom order, and IPv6 addresses. They must all parse correctly. Tests: Task 3 `test_windows_line_endings_blank_lines_and_no_final_newline_are_fine`; Task 2 `test_a_first_line_of_field_names_is_a_header_in_any_order_with_unknown_fields`.
3. **An attack straddling a 5-minute boundary**, for example 120 ports scanned between 12:04 and 12:06. It must be detected. Tests: Task 4 `test_windows_slide_so_activity_across_a_five_minute_boundary_counts`; Task 5 `test_a_scan_straddling_a_five_minute_boundary_is_detected`; Task 9's `scan_across_a_window_boundary` family.
4. **A long attack.** It must give one finding per continuous episode, and the same file must always give identical findings and fingerprints, because Plan 4's idempotent reprocessing relies on that. Tests: Task 5 `test_a_long_scan_is_one_finding_and_separate_bursts_are_two`; Task 8 `test_the_same_file_always_gives_the_same_findings`.
5. **Traffic in the other direction**: servers answering many clients, and replies from ports 22 or 443 to many ephemeral ports. It must give no findings. Tests: Task 5 `test_a_server_answering_many_clients_is_not_a_scan`; Task 6 `test_server_replies_from_port_22_are_not_attempts`; Task 9 `test_background_traffic_alone_produces_no_findings`.

## Owner prerequisites

None until the PR (Task 11). Plan 2 changes no infrastructure and needs no AWS access.

---

### Task 1: Test tooling, the flow record and internal addresses

**Files:**
- Modify: `backend/pyproject.toml` (dev dependencies; the Ruff test ignores), `backend/uv.lock`, `.gitignore`
- Create: `backend/src/nettriage/domain/__init__.py`, `backend/src/nettriage/domain/flows.py`, `backend/src/nettriage/domain/addresses.py`
- Test: `backend/tests/unit/domain/test_addresses.py`

**Interfaces:**
- Produces:
  - `nettriage.domain.flows`: `IPAddress`, `Action`, `Direction`, `ICMP`, `TCP`, `UDP`, `protocol_name(protocol: int) -> str`, `FlowSource(provider, interface_id=None, vpc_id=None)`, and `NetworkFlow(src_ip, dst_ip, src_port, dst_port, protocol, packets, bytes, start, end, action, source, line_no, direction=None, tcp_flags=None)`. `NetworkFlow` is a frozen dataclass with slots; `start` and `end` are UTC-aware datetimes.
  - `nettriage.domain.addresses`: `INTERNAL_NETWORKS` and `is_internal(address: IPAddress) -> bool`.

- [ ] **Step 1: Add the test tools**

```bash
cd backend
uv add --dev hypothesis pytest-cov
```

Then, in `backend/pyproject.toml`, change `"tests/**" = ["S101"]` to `"tests/**" = ["S101", "S311"]`. Tests use seeded `random.Random` for synthetic traffic, which isn't a cryptographic use.

In the repo-root `.gitignore`, add `.hypothesis/` on its own line after `.coverage`.

- [ ] **Step 2: Write the failing test**

`backend/tests/unit/domain/test_addresses.py`:
```python
from ipaddress import ip_address

import pytest

from nettriage.domain.addresses import is_internal


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.10",
        "100.64.0.1",
        "100.127.255.255",
        "fd12:3456:789a::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_internal_ranges(address: str) -> None:
    assert is_internal(ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "172.15.255.255",
        "172.32.0.1",
        "100.63.255.255",
        "100.128.0.0",
        "192.169.0.1",
        "2001:db8::1",
        "127.0.0.1",
        "169.254.169.254",
    ],
)
def test_everything_else_is_external(address: str) -> None:
    assert not is_internal(ip_address(address))
```

- [ ] **Step 3: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_addresses.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain'`.

- [ ] **Step 4: Implement**

`backend/src/nettriage/domain/__init__.py`:
```python
"""Flow records, parsers and detectors. Pure Python: no AWS, database or HTTP imports."""
```

`backend/src/nettriage/domain/flows.py`:
```python
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
```

`backend/src/nettriage/domain/addresses.py`:
```python
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
```

- [ ] **Step 5: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_addresses.py -q`
Expected: `19 passed`.
Run: `just lint test`
Expected: Ruff and mypy are clean, and `59 passed` (40 existing + 19).

- [ ] **Step 6: Commit**

```bash
git add .gitignore backend/pyproject.toml backend/uv.lock backend/src/nettriage/domain backend/tests/unit/domain
git commit -m "feat(domain): network flow record and internal address ranges

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: One VPC Flow Logs record

**Files:**
- Create: `backend/src/nettriage/domain/parsing/__init__.py`, `backend/src/nettriage/domain/parsing/vpc_records.py`
- Test: `backend/tests/unit/domain/test_vpc_records.py`

**Interfaces:**
- Consumes: `NetworkFlow`, `FlowSource`, `Action`, `Direction` and `IPAddress` (Task 1).
- Produces, in `nettriage.domain.parsing.vpc_records`:
  - `DEFAULT_V2_FIELDS`, `REQUIRED_FIELDS`, `SKIPPED_STATUSES`;
  - `Layout(fields: tuple[str, ...], has_header: bool)` with `.position(name) -> int | None`;
  - `layout_for(first_line: str) -> Layout`, which raises `RecordError` for a header that lacks required fields;
  - `parse_record(line: str, layout: Layout, line_no: int) -> NetworkFlow`, which raises `SkippedRecord` for NODATA and SKIPDATA, and `RecordError(reason)` otherwise.
  - The `reason` codes are `field_count`, `missing_field`, `bad_address`, `bad_port`, `bad_protocol`, `bad_count`, `bad_time`, `start_after_end` and `bad_action`.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/domain/test_vpc_records.py`:
```python
from datetime import UTC, datetime
from ipaddress import ip_address

import pytest

from nettriage.domain.flows import FlowSource
from nettriage.domain.parsing.vpc_records import (
    DEFAULT_V2_FIELDS,
    Layout,
    RecordError,
    SkippedRecord,
    layout_for,
    parse_record,
)

V2 = Layout(DEFAULT_V2_FIELDS, has_header=False)
GOOD = (
    "2 123456789012 eni-0a1b2c3d 10.0.1.5 203.0.113.9 49152 443 6 12 3400 "
    "1790000000 1790000060 ACCEPT OK"
)


def test_a_default_v2_record_becomes_a_network_flow() -> None:
    flow = parse_record(GOOD, V2, line_no=7)

    assert flow.src_ip == ip_address("10.0.1.5")
    assert flow.dst_ip == ip_address("203.0.113.9")
    assert (flow.src_port, flow.dst_port, flow.protocol) == (49152, 443, 6)
    assert (flow.packets, flow.bytes) == (12, 3400)
    assert flow.start == datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)
    assert flow.end == datetime(2026, 9, 21, 14, 14, 20, tzinfo=UTC)
    assert flow.action == "ACCEPT"
    assert flow.source == FlowSource(provider="aws_vpc", interface_id="eni-0a1b2c3d")
    assert flow.line_no == 7
    assert flow.direction is None
    assert flow.tcp_flags is None


def test_a_first_line_of_field_names_is_a_header_in_any_order_with_unknown_fields() -> None:
    layout = layout_for(
        "version vpc-id srcaddr dstaddr srcport dstport protocol packets bytes start end "
        "action tcp-flags flow-direction traffic-path log-status"
    )

    flow = parse_record(
        "5 vpc-0abc 2001:db8::5 fd00::9 51000 22 6 4 240 1790000000 1790000001 REJECT 2 "
        "ingress 1 OK",
        layout,
        line_no=2,
    )

    assert layout.has_header
    assert flow.src_ip == ip_address("2001:db8::5")
    assert flow.dst_ip == ip_address("fd00::9")
    assert flow.source.vpc_id == "vpc-0abc"
    assert flow.source.interface_id is None
    assert flow.direction == "ingress"
    assert flow.tcp_flags == 2


def test_a_record_first_line_means_the_default_v2_format() -> None:
    assert layout_for(GOOD) == V2


def test_a_header_missing_required_fields_is_refused() -> None:
    with pytest.raises(RecordError, match="header lacks required fields: action, bytes"):
        layout_for("srcaddr dstaddr srcport dstport protocol packets start end")


def test_packet_level_addresses_replace_the_interface_addresses() -> None:
    layout = layout_for(
        "srcaddr dstaddr pkt-srcaddr pkt-dstaddr srcport dstport protocol packets bytes "
        "start end action"
    )

    flow = parse_record(
        "10.0.9.9 52.1.2.3 10.0.1.5 - 40000 443 6 3 120 1790000000 1790000002 ACCEPT",
        layout,
        line_no=2,
    )

    assert flow.src_ip == ip_address("10.0.1.5")
    assert flow.dst_ip == ip_address("52.1.2.3")


@pytest.mark.parametrize("status", ["NODATA", "SKIPDATA"])
def test_nodata_and_skipdata_records_are_skipped(status: str) -> None:
    line = f"2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - {status}"

    with pytest.raises(SkippedRecord):
        parse_record(line, V2, line_no=3)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({3: "10.0.1.999"}, "bad_address"),
        ({4: "-"}, "missing_field"),
        ({5: "65536"}, "bad_port"),
        ({6: "-1"}, "bad_port"),
        ({7: "256"}, "bad_protocol"),
        ({8: "+12"}, "bad_count"),
        ({9: "1_000"}, "bad_count"),
        ({8: "٣"}, "bad_count"),
        ({10: "soon"}, "bad_time"),
        ({10: "99999999999999"}, "bad_time"),
        ({10: "1790000061"}, "start_after_end"),
        ({12: "accept"}, "bad_action"),
    ],
)
def test_invalid_records_are_refused_with_a_reason(change: dict[int, str], reason: str) -> None:
    values = GOOD.split()
    for index, replacement in change.items():
        values[index] = replacement

    with pytest.raises(RecordError) as caught:
        parse_record(" ".join(values), V2, line_no=4)

    assert caught.value.reason == reason


def test_a_record_with_the_wrong_number_of_fields_is_refused() -> None:
    with pytest.raises(RecordError, match="field_count"):
        parse_record(GOOD + " extra", V2, line_no=5)


def test_optional_fields_that_do_not_parse_count_as_missing() -> None:
    layout = layout_for(
        "srcaddr dstaddr srcport dstport protocol packets bytes start end action "
        "tcp-flags flow-direction pkt-srcaddr"
    )

    flow = parse_record(
        "10.0.1.5 52.1.2.3 40000 443 6 3 120 1790000000 1790000002 ACCEPT x sideways nope",
        layout,
        line_no=2,
    )

    assert flow.tcp_flags is None
    assert flow.direction is None
    assert flow.src_ip == ip_address("10.0.1.5")
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_vpc_records.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.parsing'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/parsing/__init__.py`:
```python
"""Parsers from uploaded log files to `NetworkFlow` records."""
```

`backend/src/nettriage/domain/parsing/vpc_records.py`:
```python
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
```

- [ ] **Step 4: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_vpc_records.py -q`
Expected: `21 passed`.
Run: `just lint test`
Expected: clean, and `80 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/domain/parsing backend/tests/unit/domain/test_vpc_records.py
git commit -m "feat(domain): parse and validate one VPC Flow Logs record

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Streaming a whole flow log file

**Files:**
- Create: `backend/src/nettriage/domain/parsing/vpc_flow_logs.py`
- Test: `backend/tests/unit/domain/test_vpc_flow_logs.py`

**Interfaces:**
- Consumes: `Layout`, `RecordError`, `SkippedRecord`, `layout_for` and `parse_record` (Task 2).
- Produces, in `nettriage.domain.parsing.vpc_flow_logs`:
  - `ParseLimits(max_decompressed_bytes=250 MB, max_rows=2_000_000, max_line_bytes=4 KB)`;
  - `RejectedSample(line_no, reason, text)`;
  - `ParseResult(flows, rows_parsed, rows_rejected, rows_skipped, rejected_samples, has_header)`;
  - `FlowLogError(code, detail)`, where `code` is `"not_a_flow_log"` or `"limit_exceeded"`;
  - `parse_flow_log(stream: BinaryIO, limits: ParseLimits | None = None) -> ParseResult`.

  Plan 4 passes the S3 object's streaming body as `stream`. It is read once, front to back, and never seeked.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/domain/test_vpc_flow_logs.py`:
```python
import gzip
import io
import zlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nettriage.domain.parsing.vpc_flow_logs import (
    FlowLogError,
    ParseLimits,
    ParseResult,
    parse_flow_log,
)

HEADER = (
    "version account-id interface-id srcaddr dstaddr srcport dstport protocol packets bytes "
    "start end action log-status"
)


def record(n: int, action: str = "ACCEPT") -> str:
    return (
        f"2 123456789012 eni-0a1b2c3d 10.0.1.{n % 250 + 1} 203.0.113.9 {40000 + n} 443 6 "
        f"12 3400 1790000000 1790000060 {action} OK"
    )


def parse(text: str | bytes, limits: ParseLimits | None = None) -> ParseResult:
    data = text.encode() if isinstance(text, str) else text
    return parse_flow_log(io.BytesIO(data), limits)


def test_plain_text_with_a_header_parses_every_record() -> None:
    result = parse("\n".join([HEADER, record(1), record(2), record(3)]) + "\n")

    assert result.has_header
    assert result.rows_parsed == 3
    assert [flow.line_no for flow in result.flows] == [2, 3, 4]
    assert result.rows_rejected == result.rows_skipped == 0


def test_gzip_is_detected_from_its_magic_bytes() -> None:
    data = gzip.compress(("\n".join([record(1), record(2)]) + "\n").encode())

    result = parse(data)

    assert not result.has_header
    assert result.rows_parsed == 2


def test_concatenated_gzip_members_are_all_read() -> None:
    data = gzip.compress((record(1) + "\n").encode()) + gzip.compress((record(2) + "\n").encode())

    assert parse(data).rows_parsed == 2


def test_windows_line_endings_blank_lines_and_no_final_newline_are_fine() -> None:
    text = f"{HEADER}\r\n\r\n{record(1)}\r\n   \r\n{record(2)}"

    result = parse(text)

    assert result.rows_parsed == 2
    assert [flow.line_no for flow in result.flows] == [3, 5]


def test_nodata_and_skipdata_records_are_counted_not_parsed() -> None:
    nodata = "2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - NODATA"
    skipdata = "2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - SKIPDATA"

    result = parse("\n".join([record(1), nodata, skipdata]))

    assert (result.rows_parsed, result.rows_skipped, result.rows_rejected) == (1, 2, 0)


def test_a_file_of_only_nodata_records_is_a_valid_empty_log() -> None:
    nodata = "2 123456789012 eni-0a1b2c3d - - - - - - - 1790000000 1790000060 - NODATA"

    result = parse(nodata)

    assert result.flows == []
    assert result.rows_skipped == 1


def test_up_to_five_percent_invalid_records_are_rejected_and_sampled() -> None:
    lines = [record(n) for n in range(95)] + ["not a record"] * 5

    result = parse("\n".join(lines))

    assert (result.rows_parsed, result.rows_rejected) == (95, 5)
    assert [sample.line_no for sample in result.rejected_samples] == [96, 97, 98, 99, 100]
    assert result.rejected_samples[0].reason == "field_count"
    assert result.rejected_samples[0].text == "not a record"


def test_more_than_five_percent_invalid_records_is_not_a_flow_log() -> None:
    lines = [record(n) for n in range(94)] + ["not a record"] * 6

    with pytest.raises(FlowLogError) as caught:
        parse("\n".join(lines))

    assert caught.value.code == "not_a_flow_log"
    assert "6 of 100 records" in caught.value.detail


def test_at_most_twenty_rejected_samples_of_at_most_120_characters_are_kept() -> None:
    lines = [record(n) for n in range(1000)] + ["x" * 500] * 40

    result = parse("\n".join(lines))

    assert result.rows_rejected == 40
    assert len(result.rejected_samples) == 20
    assert all(len(sample.text) == 120 for sample in result.rejected_samples)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n\n  \n",
        HEADER.encode() + b"\n",
        b"name,email,amount\nalice,a@example.com,10\nbob,b@example.com,20\n",
        b'{"flows": [{"src": "10.0.0.1"}]}',
        b"hello world\n",
        b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64,
    ],
    ids=["empty", "blank", "header-only", "csv", "json", "prose", "png"],
)
def test_files_that_are_not_flow_logs_say_so(content: bytes) -> None:
    with pytest.raises(FlowLogError) as caught:
        parse(content)

    assert caught.value.code == "not_a_flow_log"
    assert caught.value.detail


def test_corrupt_gzip_is_not_a_flow_log() -> None:
    data = gzip.compress((record(1) + "\n").encode())

    with pytest.raises(FlowLogError) as caught:
        parse(data[:-8] + b"garbage!")

    assert caught.value.code == "not_a_flow_log"


def test_a_too_long_line_after_valid_records_exceeds_the_limit() -> None:
    limits = ParseLimits(max_line_bytes=200)

    with pytest.raises(FlowLogError) as caught:
        parse(record(1) + "\n" + "9" * 201 + "\n", limits)

    assert caught.value.code == "limit_exceeded"


def test_too_many_rows_exceed_the_limit() -> None:
    limits = ParseLimits(max_rows=3)

    with pytest.raises(FlowLogError) as caught:
        parse("\n".join(record(n) for n in range(4)), limits)

    assert caught.value.code == "limit_exceeded"


def test_a_gzip_bomb_stops_at_the_decompressed_size_limit() -> None:
    compressor = zlib.compressobj(wbits=31)
    line = (record(1) + "\n").encode()
    bomb = b"".join(compressor.compress(line * 1000) for _ in range(200)) + compressor.flush()
    limits = ParseLimits(max_decompressed_bytes=1024 * 1024)

    with pytest.raises(FlowLogError) as caught:
        parse(bomb, limits)

    assert len(bomb) < 200_000
    assert caught.value.code == "limit_exceeded"


def outcome(data: str | bytes, limits: ParseLimits) -> ParseResult | str:
    """The parse result, or the failure code: never any other exception."""
    try:
        return parse(data, limits)
    except FlowLogError as exc:
        return exc.code


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=4096))
def test_arbitrary_bytes_never_crash_the_parser(data: bytes) -> None:
    result = outcome(
        data, ParseLimits(max_decompressed_bytes=8192, max_rows=50, max_line_bytes=256)
    )

    if isinstance(result, str):
        assert result in ("not_a_flow_log", "limit_exceeded")
    else:
        assert len(result.flows) <= 50
        assert len(result.rejected_samples) <= 20


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.one_of(
            st.builds(record, st.integers(0, 10_000), st.sampled_from(["ACCEPT", "REJECT"])),
            st.text(alphabet=st.characters(codec="ascii"), max_size=160),
        ),
        max_size=60,
    )
)
def test_mixed_lines_respect_the_limits(lines: list[str]) -> None:
    result = outcome("\n".join(lines), ParseLimits(max_rows=40, max_line_bytes=200))

    if isinstance(result, str):
        assert result in ("not_a_flow_log", "limit_exceeded")
    else:
        assert result.rows_parsed + result.rows_rejected + result.rows_skipped <= 40
        assert all(len(sample.text) <= 120 for sample in result.rejected_samples)
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_vpc_flow_logs.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.parsing.vpc_flow_logs'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/parsing/vpc_flow_logs.py`:
```python
"""Stream a VPC Flow Logs file, plain or gzip, into `NetworkFlow` records (spec §8.1, §8.6)."""

from __future__ import annotations

import gzip
import io
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO, Literal

from nettriage.domain.flows import NetworkFlow
from nettriage.domain.parsing.vpc_records import (
    Layout,
    RecordError,
    SkippedRecord,
    layout_for,
    parse_record,
)

GZIP_MAGIC = b"\x1f\x8b"
CHUNK_BYTES = 64 * 1024
MAX_REJECTED_SAMPLES = 20
SAMPLE_CHARS = 120
MAX_INVALID_SHARE = 0.05

type FailureCode = Literal["not_a_flow_log", "limit_exceeded"]


@dataclass(frozen=True)
class ParseLimits:
    """Spec §8.1's streaming limits. Sizes are binary: 1 MB = 1,048,576 bytes."""

    max_decompressed_bytes: int = 250 * 1024 * 1024
    max_rows: int = 2_000_000
    max_line_bytes: int = 4 * 1024


@dataclass(frozen=True)
class RejectedSample:
    line_no: int
    reason: str
    text: str


@dataclass(frozen=True)
class ParseResult:
    flows: list[NetworkFlow]
    rows_parsed: int
    rows_rejected: int
    rows_skipped: int
    rejected_samples: list[RejectedSample]
    has_header: bool


class FlowLogError(Exception):
    """The upload fails with `code`; `detail` is a readable reason for the UI."""

    def __init__(self, code: FailureCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: FailureCode = code
        self.detail = detail


class _LineTooLong(Exception):
    pass


class _TooLarge(Exception):
    pass


def parse_flow_log(stream: BinaryIO, limits: ParseLimits | None = None) -> ParseResult:
    """Parse `stream` completely or raise `FlowLogError`. Gzip is detected from the first two
    bytes, never from a file name. Memory stays bounded by the limits: nothing is read past
    them."""
    limits = limits or ParseLimits()
    parser = _Parser(limits)
    try:
        for raw in _lines(_decompressed(stream), limits):
            parser.feed(raw)
    except _LineTooLong:
        if parser.rows_parsed == 0:
            raise FlowLogError(
                "not_a_flow_log", "the file doesn't contain flow log lines"
            ) from None
        raise FlowLogError(
            "limit_exceeded", f"a line is longer than {limits.max_line_bytes} bytes"
        ) from None
    except _TooLarge:
        raise FlowLogError(
            "limit_exceeded",
            f"the file is larger than {limits.max_decompressed_bytes // (1024 * 1024)} MB "
            "uncompressed",
        ) from None
    except gzip.BadGzipFile, EOFError, zlib.error:
        raise FlowLogError(
            "not_a_flow_log", "the file looks like gzip but can't be decompressed"
        ) from None
    return parser.finish()


class _Rewound:
    """`stream` with the bytes already read put back in front, for `gzip.GzipFile`."""

    def __init__(self, head: bytes, stream: BinaryIO) -> None:
        self._head = head
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        if not self._head:
            return self._stream.read(size)
        if size < 0:
            data, self._head = self._head + self._stream.read(), b""
            return data
        data, self._head = self._head[:size], self._head[size:]
        return data

    def seek(self, offset: int, /) -> int:
        # GzipFile reads front to back and never seeks; uploads can't rewind anyway.
        raise io.UnsupportedOperation("a flow log stream is read once, front to back")


def _decompressed(stream: BinaryIO) -> Iterator[bytes]:
    head = stream.read(len(GZIP_MAGIC))
    if head == GZIP_MAGIC:
        with gzip.GzipFile(fileobj=_Rewound(head, stream), mode="rb") as unzipped:
            while chunk := unzipped.read(CHUNK_BYTES):
                yield chunk
        return
    if head:
        yield head
    while chunk := stream.read(CHUNK_BYTES):
        yield chunk


def _lines(chunks: Iterable[bytes], limits: ParseLimits) -> Iterator[bytes]:
    total = 0
    pending = b""
    for chunk in chunks:
        total += len(chunk)
        if total > limits.max_decompressed_bytes:
            raise _TooLarge
        *complete, pending = (pending + chunk).split(b"\n")
        for line in complete:
            if len(line) > limits.max_line_bytes:
                raise _LineTooLong
            yield line
        if len(pending) > limits.max_line_bytes:
            raise _LineTooLong
    if pending:
        yield pending


class _Parser:
    def __init__(self, limits: ParseLimits) -> None:
        self._limits = limits
        self._layout: Layout | None = None
        self._line_no = 0
        self._rows = 0
        self.rows_parsed = 0
        self._rows_rejected = 0
        self._rows_skipped = 0
        self._flows: list[NetworkFlow] = []
        self._samples: list[RejectedSample] = []

    def feed(self, raw: bytes) -> None:
        self._line_no += 1
        raw = raw.rstrip(b"\r")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            self._count_row()
            self._reject(raw, "encoding")
            return
        if not text.strip():
            return
        if self._layout is None:
            try:
                self._layout = layout_for(text)
            except RecordError as exc:
                raise FlowLogError("not_a_flow_log", exc.reason) from None
            if self._layout.has_header:
                return
        self._count_row()
        try:
            self._flows.append(parse_record(text, self._layout, self._line_no))
            self.rows_parsed += 1
        except SkippedRecord:
            self._rows_skipped += 1
        except RecordError as exc:
            self._reject(raw, exc.reason)

    def finish(self) -> ParseResult:
        records = self.rows_parsed + self._rows_rejected
        if records == 0 and self._rows_skipped == 0:
            raise FlowLogError("not_a_flow_log", "the file contains no flow records")
        if self._rows_rejected > MAX_INVALID_SHARE * records:
            raise FlowLogError(
                "not_a_flow_log",
                f"{self._rows_rejected} of {records} records aren't valid flow log records",
            )
        return ParseResult(
            flows=self._flows,
            rows_parsed=self.rows_parsed,
            rows_rejected=self._rows_rejected,
            rows_skipped=self._rows_skipped,
            rejected_samples=self._samples,
            has_header=self._layout is not None and self._layout.has_header,
        )

    def _count_row(self) -> None:
        self._rows += 1
        if self._rows > self._limits.max_rows:
            raise FlowLogError(
                "limit_exceeded", f"the file has more than {self._limits.max_rows:,} rows"
            )

    def _reject(self, raw: bytes, reason: str) -> None:
        self._rows_rejected += 1
        if len(self._samples) < MAX_REJECTED_SAMPLES:
            text = raw[:SAMPLE_CHARS].decode("ascii", errors="replace")
            self._samples.append(RejectedSample(self._line_no, reason, text))
```

- [ ] **Step 4: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_vpc_flow_logs.py -q`
Expected: `22 passed`. The two Hypothesis property tests generate 500 inputs between them, and neither crashes nor exceeds a limit.
Run: `just lint test`
Expected: clean, and `102 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/domain/parsing/vpc_flow_logs.py backend/tests/unit/domain/test_vpc_flow_logs.py
git commit -m "feat(domain): stream plain or gzip flow logs within the spec's limits

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Findings, fingerprints, evidence and sliding windows

**Files:**
- Create: `backend/src/nettriage/domain/detection/__init__.py`, `backend/src/nettriage/domain/detection/model.py`, `backend/src/nettriage/domain/detection/windows.py`
- Create (test helper): `backend/tests/unit/domain/flowmaker.py`
- Test: `backend/tests/unit/domain/test_detection_model.py`

**Interfaces:**
- Consumes: `NetworkFlow` and `IPAddress` (Task 1).
- Produces, in `nettriage.domain.detection.model`:
  - `Severity` (a StrEnum: `low`, `medium`, `high`, `critical`) and `SEVERITY_RANK`;
  - `Finding(detector_id, detector_version, fingerprint, severity, title, src_ip, dst_ip, dst_port, protocol, window_start, window_end, metrics, candidate_techniques, evidence)`;
  - `DetectorInfo(id, version, name, description, candidate_techniques, run)`;
  - `fingerprint(detector_id, version, entities, window_start) -> str`;
  - `flow_order(flow)`;
  - `sample_evidence(flows) -> tuple[NetworkFlow, ...]`;
  - `rank_and_cap(findings, limit=50) -> tuple[list[Finding], int]`;
  - `MAX_FINDINGS_PER_UPLOAD = 50`.
- Produces, in `nettriage.domain.detection.windows`: `WINDOW`, `Episode(flows, peak_distinct)` and `sliding_episodes(flows, key, min_distinct, is_marked=None, min_marked_percent=0) -> list[Episode]`.
- Produces, as a test helper: `flowmaker.make_flow(src, dst, dst_port, *, at, duration, src_port, protocol, packets, bytes, action)` and `T0`. Test files in the same directory import it as `from flowmaker import make_flow`.

- [ ] **Step 1: Write the test helper and the failing test**

`backend/tests/unit/domain/flowmaker.py`:
```python
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
    )
```

`backend/tests/unit/domain/test_detection_model.py`:
```python
from dataclasses import replace
from datetime import timedelta
from ipaddress import ip_address

from flowmaker import T0, make_flow

from nettriage.domain.detection.model import (
    Finding,
    Severity,
    fingerprint,
    rank_and_cap,
    sample_evidence,
)
from nettriage.domain.detection.windows import sliding_episodes
from nettriage.domain.flows import NetworkFlow


def test_fingerprints_ignore_where_the_window_starts_within_five_minutes() -> None:
    first = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0 + timedelta(seconds=10))
    same_bucket = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0 + timedelta(seconds=299))
    next_bucket = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0 + timedelta(seconds=300))

    assert first == same_bucket
    assert first != next_bucket
    assert len(first) == 64


def test_fingerprints_depend_on_detector_version_and_entities() -> None:
    base = fingerprint("port_scan", 1, ["vertical", "1.2.3.4"], T0)

    assert base != fingerprint("port_scan", 2, ["vertical", "1.2.3.4"], T0)
    assert base != fingerprint("outbound_volume", 1, ["vertical", "1.2.3.4"], T0)
    assert base != fingerprint("port_scan", 1, ["vertical", "1.2.3.5"], T0)
    # Entities are separated, so ("1.2", "3.4") can't collide with ("1.23", ".4").
    assert fingerprint("x", 1, ["1.2", "3.4"], T0) != fingerprint("x", 1, ["1.23", ".4"], T0)


def test_small_evidence_sets_are_kept_whole_in_time_order() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", 80 + n, at=40 - n) for n in range(40)]

    evidence = sample_evidence(flows)

    assert len(evidence) == 40
    assert [flow.start for flow in evidence] == sorted(flow.start for flow in flows)


def test_large_evidence_sets_keep_the_first_ten_last_ten_and_thirty_between() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", n, at=n) for n in range(1000)]

    evidence = sample_evidence(reversed(flows))

    assert len(evidence) == 50
    assert list(evidence[:10]) == flows[:10]
    assert list(evidence[-10:]) == flows[-10:]
    middle = evidence[10:40]
    assert len(set(middle)) == 30
    assert middle[0] == flows[10]
    assert middle[-1] == flows[989]
    assert [flow.start for flow in evidence] == sorted(flow.start for flow in evidence)


def finding(severity: Severity, at: int, fp: str) -> Finding:
    return Finding(
        detector_id="port_scan",
        detector_version=1,
        fingerprint=fp,
        severity=severity,
        title="t",
        src_ip=ip_address("1.2.3.4"),
        dst_ip=None,
        dst_port=None,
        protocol=None,
        window_start=T0 + timedelta(seconds=at),
        window_end=T0 + timedelta(seconds=at + 1),
        metrics={},
        candidate_techniques=(),
        evidence=(),
    )


def test_ranking_puts_high_severity_first_then_earliest_and_caps() -> None:
    findings = [
        finding(Severity.MEDIUM, 0, "a"),
        finding(Severity.HIGH, 50, "b"),
        finding(Severity.HIGH, 10, "c"),
        finding(Severity.LOW, 0, "d"),
    ]

    kept, truncated = rank_and_cap(findings, limit=3)

    assert [f.fingerprint for f in kept] == ["c", "b", "a"]
    assert truncated == 1


def test_ranking_keeps_one_finding_per_fingerprint() -> None:
    duplicate = finding(Severity.MEDIUM, 0, "a")

    kept, truncated = rank_and_cap([duplicate, replace(duplicate, title="again")])

    assert len(kept) == 1
    assert truncated == 0


def test_windows_slide_so_activity_across_a_five_minute_boundary_counts() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", port, at=240 + port) for port in range(100)]

    episodes = sliding_episodes(flows, key=lambda f: f.dst_port, min_distinct=100)

    assert len(episodes) == 1
    assert episodes[0].peak_distinct == 100


def test_activity_spread_wider_than_five_minutes_does_not_qualify() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", port, at=port * 4) for port in range(100)]

    assert sliding_episodes(flows, key=lambda f: f.dst_port, min_distinct=100) == []


def test_overlapping_qualifying_windows_merge_into_one_episode() -> None:
    flows = [make_flow("1.2.3.4", "10.0.0.1", n % 500, at=n) for n in range(3600)]

    episodes = sliding_episodes(flows, key=lambda f: f.dst_port, min_distinct=100)

    assert len(episodes) == 1
    assert len(episodes[0].flows) == 3600


def test_separate_bursts_are_separate_episodes() -> None:
    first = [make_flow("1.2.3.4", "10.0.0.1", port, at=port) for port in range(120)]
    second = [make_flow("1.2.3.4", "10.0.0.1", port, at=3600 + port) for port in range(120)]

    episodes = sliding_episodes(first + second, key=lambda f: f.dst_port, min_distinct=100)

    assert [len(episode.flows) for episode in episodes] == [120, 120]


def test_the_marked_share_must_hold_in_the_window() -> None:
    flows = [
        make_flow("1.2.3.4", "10.0.0.1", port, at=port, action="ACCEPT" if port < 30 else "REJECT")
        for port in range(100)
    ]

    def rejected(flow: NetworkFlow) -> bool:
        return flow.action == "REJECT"

    assert sliding_episodes(flows, lambda f: f.dst_port, 100, rejected, 80) == []
    assert len(sliding_episodes(flows, lambda f: f.dst_port, 100, rejected, 70)) == 1
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_detection_model.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.detection'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/detection/__init__.py`:
```python
"""Detectors that turn `NetworkFlow` records into findings (spec §8.2)."""
```

`backend/src/nettriage/domain/detection/model.py`:
```python
"""What detectors produce (spec §5 `findings` and `finding_evidence`) and the rules they
share (spec §8.2): fingerprints, evidence sampling, and ranking with the per-upload cap."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from nettriage.domain.flows import IPAddress, NetworkFlow

FINGERPRINT_BUCKET_SECONDS = 300
EVIDENCE_HEAD = 10
EVIDENCE_TAIL = 10
EVIDENCE_MIDDLE = 30
MAX_EVIDENCE = EVIDENCE_HEAD + EVIDENCE_MIDDLE + EVIDENCE_TAIL
MAX_FINDINGS_PER_UPLOAD = 50

type MetricValue = int | float | bool | str


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_RANK: Mapping[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


@dataclass(frozen=True)
class Finding:
    """One detection. `dst_ip`, `dst_port` and `protocol` are None when the finding spans many
    values (a horizontal scan has no single host, a vertical scan no single port)."""

    detector_id: str
    detector_version: int
    fingerprint: str
    severity: Severity
    title: str
    src_ip: IPAddress
    dst_ip: IPAddress | None
    dst_port: int | None
    protocol: int | None
    window_start: datetime
    window_end: datetime
    metrics: Mapping[str, MetricValue]
    candidate_techniques: tuple[str, ...]
    evidence: tuple[NetworkFlow, ...]


type DetectorRun = Callable[[Sequence[NetworkFlow]], list[Finding]]


@dataclass(frozen=True)
class DetectorInfo:
    """Reference data for the `detectors` table (synced from code at deploy time)."""

    id: str
    version: int
    name: str
    description: str
    candidate_techniques: tuple[str, ...]
    run: DetectorRun


def fingerprint(
    detector_id: str, version: int, entities: Iterable[str], window_start: datetime
) -> str:
    """`sha256(detector_id, version, key entities, window start rounded down to 5 minutes)`, so
    re-processing the same file yields the same fingerprints."""
    seconds = int(window_start.timestamp())
    bucket = seconds - seconds % FINGERPRINT_BUCKET_SECONDS
    canonical = "\x1f".join([detector_id, str(version), *entities, str(bucket)])
    return hashlib.sha256(canonical.encode()).hexdigest()


def flow_order(flow: NetworkFlow) -> tuple[datetime, int]:
    return flow.start, flow.line_no


def sample_evidence(flows: Iterable[NetworkFlow]) -> tuple[NetworkFlow, ...]:
    """Up to 50 flows in time order: the first 10, the last 10, and 30 evenly spaced between."""
    ordered = sorted(flows, key=flow_order)
    if len(ordered) <= MAX_EVIDENCE:
        return tuple(ordered)
    middle = ordered[EVIDENCE_HEAD : len(ordered) - EVIDENCE_TAIL]
    span = len(middle) - 1
    picked = [middle[i * span // (EVIDENCE_MIDDLE - 1)] for i in range(EVIDENCE_MIDDLE)]
    return tuple(ordered[:EVIDENCE_HEAD] + picked + ordered[-EVIDENCE_TAIL:])


def rank_and_cap(
    findings: Iterable[Finding], limit: int = MAX_FINDINGS_PER_UPLOAD
) -> tuple[list[Finding], int]:
    """Highest severity first, then earliest; one finding per fingerprint. Returns the kept
    findings and how many were cut (`findings_truncated`)."""
    ordered = sorted(
        findings,
        key=lambda f: (-SEVERITY_RANK[f.severity], f.window_start, f.detector_id, f.fingerprint),
    )
    unique: list[Finding] = []
    seen: set[str] = set()
    for finding in ordered:
        if finding.fingerprint not in seen:
            seen.add(finding.fingerprint)
            unique.append(finding)
    return unique[:limit], max(0, len(unique) - limit)
```

`backend/src/nettriage/domain/detection/windows.py`:
```python
"""Sliding 5-minute windows over flow start times (spec §8.2, "in any 5-minute window").

A window holds the flows that started less than 5 minutes after its first flow. Every window
that meets a detector's rule qualifies; qualifying windows that share flows merge into one
episode, so an attack lasting an hour is one finding, not sixty."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from nettriage.domain.flows import NetworkFlow

WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class Episode:
    flows: Sequence[NetworkFlow]
    peak_distinct: int


def sliding_episodes(
    flows: Sequence[NetworkFlow],
    key: Callable[[NetworkFlow], Hashable],
    min_distinct: int,
    is_marked: Callable[[NetworkFlow], bool] | None = None,
    min_marked_percent: int = 0,
) -> list[Episode]:
    """`flows` must be sorted by `flow_order`. A window qualifies when it holds at least
    `min_distinct` distinct `key` values and at least `min_marked_percent` of its flows satisfy
    `is_marked`. `peak_distinct` is the most distinct values any qualifying window held."""
    if len(flows) < min_distinct:
        return []
    counts: Counter[Hashable] = Counter()
    marked = 0
    left = 0
    spans: list[list[int]] = []  # [first index, last index, peak distinct]
    for right, flow in enumerate(flows):
        counts[key(flow)] += 1
        marked += 1 if is_marked is None or is_marked(flow) else 0
        while flow.start - flows[left].start >= WINDOW:
            leaving = flows[left]
            leaving_key = key(leaving)
            counts[leaving_key] -= 1
            if counts[leaving_key] == 0:
                del counts[leaving_key]
            marked -= 1 if is_marked is None or is_marked(leaving) else 0
            left += 1
        size = right - left + 1
        if len(counts) >= min_distinct and marked * 100 >= min_marked_percent * size:
            if spans and left <= spans[-1][1]:
                spans[-1][1] = right
                spans[-1][2] = max(spans[-1][2], len(counts))
            else:
                spans.append([left, right, len(counts)])
    return [Episode(flows[first : last + 1], peak) for first, last, peak in spans]
```

- [ ] **Step 4: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_detection_model.py -q`
Expected: `11 passed`.
Run: `just lint test`
Expected: clean, and `113 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/domain/detection backend/tests/unit/domain/flowmaker.py backend/tests/unit/domain/test_detection_model.py
git commit -m "feat(domain): findings, fingerprints, evidence sampling and sliding windows

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The `port_scan` detector

**Files:**
- Create: `backend/src/nettriage/domain/detection/port_scan.py`
- Test: `backend/tests/unit/domain/test_port_scan.py`

**Interfaces:**
- Consumes: `is_internal` (Task 1); `DetectorInfo`, `Finding`, `Severity`, `fingerprint`, `flow_order`, `sample_evidence`, `Episode` and `sliding_episodes` (Task 4).
- Produces: `detect_port_scans(flows) -> list[Finding]` and `INFO: DetectorInfo`.
  - `metrics` keys: `variant` (`vertical` or `horizontal`), `peak_distinct`, `distinct_total`, `flows`, `rejected`, `scan_like_percent` and `source_internal`.
  - Fingerprint entities: `[variant, src, dst, protocol]` for vertical scans, and `[variant, src, port, protocol]` for horizontal scans.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/domain/test_port_scan.py`:
```python
from ipaddress import ip_address

from flowmaker import make_flow

from nettriage.domain.detection.model import Severity
from nettriage.domain.detection.port_scan import detect_port_scans
from nettriage.domain.flows import UDP, NetworkFlow

ATTACKER = "198.51.100.7"
TARGET = "10.0.1.20"


def vertical(
    src: str, ports: int, *, start: float = 0.0, spacing: float = 1.0
) -> list[NetworkFlow]:
    return [make_flow(src, TARGET, 1 + n, at=start + n * spacing) for n in range(ports)]


def horizontal(src: str, hosts: int, port: int = 445) -> list[NetworkFlow]:
    return [make_flow(src, f"10.0.2.{1 + n}", port, at=n) for n in range(hosts)]


def test_an_external_vertical_scan_is_a_medium_finding() -> None:
    [finding] = detect_port_scans(vertical(ATTACKER, 150))

    assert finding.detector_id == "port_scan"
    assert finding.severity is Severity.MEDIUM
    assert finding.src_ip == ip_address(ATTACKER)
    assert finding.dst_ip == ip_address(TARGET)
    assert finding.dst_port is None
    assert finding.protocol == 6
    assert finding.metrics["variant"] == "vertical"
    assert finding.metrics["peak_distinct"] == 150
    assert finding.candidate_techniques == ("T1595", "T1595.001")
    assert finding.title == f"Port scan of {TARGET} from {ATTACKER}: 150 TCP ports in 5 minutes"
    assert len(finding.evidence) == 50


def test_an_external_scan_of_a_thousand_ports_is_high() -> None:
    [finding] = detect_port_scans(vertical(ATTACKER, 1_200, spacing=0.2))

    assert finding.severity is Severity.HIGH


def test_an_internal_source_is_always_high_with_network_service_discovery() -> None:
    [finding] = detect_port_scans(horizontal("10.0.9.9", 60))

    assert finding.severity is Severity.HIGH
    assert finding.metrics["variant"] == "horizontal"
    assert finding.dst_ip is None
    assert finding.dst_port == 445
    assert finding.candidate_techniques == ("T1046",)


def test_ninety_nine_ports_is_not_a_scan() -> None:
    assert detect_port_scans(vertical(ATTACKER, 99)) == []


def test_forty_nine_hosts_is_not_a_scan() -> None:
    assert detect_port_scans(horizontal(ATTACKER, 49)) == []


def test_a_slow_scan_never_reaching_100_ports_in_5_minutes_is_not_flagged() -> None:
    assert detect_port_scans(vertical(ATTACKER, 400, spacing=4.0)) == []


def test_a_scan_straddling_a_five_minute_boundary_is_detected() -> None:
    flows = vertical(ATTACKER, 120, start=240.0, spacing=0.9)

    assert len(detect_port_scans(flows)) == 1


def test_mostly_completed_connections_are_not_a_scan() -> None:
    flows = [
        make_flow(ATTACKER, TARGET, 1 + n, at=n, packets=40, action="ACCEPT") for n in range(150)
    ]

    assert detect_port_scans(flows) == []


def test_tiny_accepted_probes_still_count_as_scan_like() -> None:
    flows = [
        make_flow(ATTACKER, TARGET, 1 + n, at=n, packets=2, action="ACCEPT") for n in range(150)
    ]

    assert len(detect_port_scans(flows)) == 1


def test_a_long_scan_is_one_finding_and_separate_bursts_are_two() -> None:
    long_scan = [make_flow(ATTACKER, TARGET, 1 + n % 1000, at=n * 0.5) for n in range(7200)]
    bursts = vertical("198.51.100.8", 120) + vertical("198.51.100.8", 120, start=7200.0)

    findings = detect_port_scans(long_scan + bursts)

    assert sorted(str(f.src_ip) for f in findings) == [
        "198.51.100.7",
        "198.51.100.8",
        "198.51.100.8",
    ]
    assert len({f.fingerprint for f in findings}) == 3


def test_a_server_answering_many_clients_is_not_a_scan() -> None:
    responses = [
        make_flow(
            TARGET,
            f"203.0.113.{1 + n % 200}",
            30_000 + n,
            at=n * 0.2,
            src_port=443,
            packets=12,
            bytes=9_000,
            action="ACCEPT",
        )
        for n in range(1_000)
    ]

    assert detect_port_scans(responses) == []


def test_udp_is_scanned_separately_and_icmp_never() -> None:
    udp = [make_flow(ATTACKER, TARGET, 1 + n, at=n, protocol=UDP) for n in range(120)]
    icmp = [make_flow(ATTACKER, f"10.0.3.{n}", 0, at=n, protocol=1) for n in range(1, 120)]

    [finding] = detect_port_scans(udp + icmp)

    assert finding.protocol == UDP
    assert "UDP ports" in finding.title


def test_findings_are_identical_when_detection_runs_twice() -> None:
    flows = vertical(ATTACKER, 150) + horizontal("10.0.9.9", 60)

    assert detect_port_scans(flows) == detect_port_scans(list(flows))
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_port_scan.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.detection.port_scan'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/detection/port_scan.py`:
```python
"""`port_scan` v1 (spec §8.2): in any 5-minute window, one source reaches at least 100 distinct
destination ports on one host (vertical) or at least 50 distinct hosts on one port
(horizontal), with at least 80% of those flows REJECT or at most 3 packets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from nettriage.domain.addresses import is_internal
from nettriage.domain.detection.model import (
    DetectorInfo,
    Finding,
    Severity,
    fingerprint,
    flow_order,
    sample_evidence,
)
from nettriage.domain.detection.windows import Episode, sliding_episodes
from nettriage.domain.flows import TCP, UDP, IPAddress, NetworkFlow, protocol_name

DETECTOR_ID = "port_scan"
VERSION = 1
VERTICAL_MIN_PORTS = 100
HORIZONTAL_MIN_HOSTS = 50
SCAN_LIKE_MIN_PERCENT = 80
SCAN_LIKE_MAX_PACKETS = 3
EXTERNAL_HIGH_MIN = 1_000
SCAN_PROTOCOLS = frozenset({TCP, UDP})
EXTERNAL_TECHNIQUES = ("T1595", "T1595.001")
INTERNAL_TECHNIQUES = ("T1046",)


def _scan_like(flow: NetworkFlow) -> bool:
    return flow.action == "REJECT" or flow.packets <= SCAN_LIKE_MAX_PACKETS


def detect_port_scans(flows: Sequence[NetworkFlow]) -> list[Finding]:
    vertical: defaultdict[tuple[IPAddress, IPAddress, int], list[NetworkFlow]] = defaultdict(list)
    horizontal: defaultdict[tuple[IPAddress, int, int], list[NetworkFlow]] = defaultdict(list)
    for flow in flows:
        if flow.protocol in SCAN_PROTOCOLS:
            vertical[(flow.src_ip, flow.dst_ip, flow.protocol)].append(flow)
            horizontal[(flow.src_ip, flow.dst_port, flow.protocol)].append(flow)

    findings: list[Finding] = []
    for (src, dst, protocol), group in vertical.items():
        if len(group) < VERTICAL_MIN_PORTS:
            continue
        group.sort(key=flow_order)
        for episode in sliding_episodes(
            group,
            key=lambda f: f.dst_port,
            min_distinct=VERTICAL_MIN_PORTS,
            is_marked=_scan_like,
            min_marked_percent=SCAN_LIKE_MIN_PERCENT,
        ):
            peak = episode.peak_distinct
            title = (
                f"Port scan of {dst} from {src}: {peak} {protocol_name(protocol)} ports "
                "in 5 minutes"
            )
            findings.append(
                _finding(
                    "vertical",
                    episode,
                    src=src,
                    dst_ip=dst,
                    dst_port=None,
                    protocol=protocol,
                    entities=[str(dst), str(protocol)],
                    distinct_total=len({f.dst_port for f in episode.flows}),
                    title=title,
                )
            )
    for (src, port, protocol), group in horizontal.items():
        if len(group) < HORIZONTAL_MIN_HOSTS:
            continue
        group.sort(key=flow_order)
        for episode in sliding_episodes(
            group,
            key=lambda f: f.dst_ip,
            min_distinct=HORIZONTAL_MIN_HOSTS,
            is_marked=_scan_like,
            min_marked_percent=SCAN_LIKE_MIN_PERCENT,
        ):
            peak = episode.peak_distinct
            title = (
                f"Port scan of {protocol_name(protocol)} port {port} across {peak} hosts "
                f"from {src} in 5 minutes"
            )
            findings.append(
                _finding(
                    "horizontal",
                    episode,
                    src=src,
                    dst_ip=None,
                    dst_port=port,
                    protocol=protocol,
                    entities=[str(port), str(protocol)],
                    distinct_total=len({f.dst_ip for f in episode.flows}),
                    title=title,
                )
            )
    return findings


def _finding(
    variant: str,
    episode: Episode,
    *,
    src: IPAddress,
    dst_ip: IPAddress | None,
    dst_port: int | None,
    protocol: int,
    entities: list[str],
    distinct_total: int,
    title: str,
) -> Finding:
    flows = episode.flows
    internal = is_internal(src)
    if internal or episode.peak_distinct >= EXTERNAL_HIGH_MIN:
        severity = Severity.HIGH
    else:
        severity = Severity.MEDIUM
    scan_like = sum(1 for flow in flows if _scan_like(flow))
    window_start = flows[0].start
    return Finding(
        detector_id=DETECTOR_ID,
        detector_version=VERSION,
        fingerprint=fingerprint(DETECTOR_ID, VERSION, [variant, str(src), *entities], window_start),
        severity=severity,
        title=title,
        src_ip=src,
        dst_ip=dst_ip,
        dst_port=dst_port,
        protocol=protocol,
        window_start=window_start,
        window_end=max(flow.end for flow in flows),
        metrics={
            "variant": variant,
            "peak_distinct": episode.peak_distinct,
            "distinct_total": distinct_total,
            "flows": len(flows),
            "rejected": sum(1 for flow in flows if flow.action == "REJECT"),
            "scan_like_percent": round(100 * scan_like / len(flows)),
            "source_internal": internal,
        },
        candidate_techniques=INTERNAL_TECHNIQUES if internal else EXTERNAL_TECHNIQUES,
        evidence=sample_evidence(flows),
    )


INFO = DetectorInfo(
    id=DETECTOR_ID,
    version=VERSION,
    name="Port scan",
    description=(
        "One source reaching at least 100 ports on one host, or one port on at least 50 hosts, "
        "within 5 minutes, mostly rejected or tiny flows."
    ),
    candidate_techniques=(*EXTERNAL_TECHNIQUES, *INTERNAL_TECHNIQUES),
    run=detect_port_scans,
)
```

- [ ] **Step 4: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_port_scan.py -q`
Expected: `13 passed`.
Run: `just lint test`
Expected: clean, and `126 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/domain/detection/port_scan.py backend/tests/unit/domain/test_port_scan.py
git commit -m "feat(domain): port_scan detector (vertical and horizontal)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The `remote_access_bruteforce` detector

**Files:**
- Create: `backend/src/nettriage/domain/detection/bruteforce.py`
- Test: `backend/tests/unit/domain/test_bruteforce.py`

**Interfaces:**
- Consumes: the same Task 4 helpers as Task 5.
- Produces: `detect_bruteforce(flows) -> list[Finding]` and `INFO: DetectorInfo`.
  - `metrics` keys: `variant` (`single` or `spray`), `peak_distinct`, `attempts`, `hosts` and `possible_success`, plus `success_bytes` and `success_seconds` when a login may have succeeded.
  - Fingerprint entities: `[variant, src, dst, port]` for a single host, and `[variant, src, port]` for spraying.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/domain/test_bruteforce.py`:
```python
from ipaddress import ip_address

from flowmaker import make_flow

from nettriage.domain.detection.bruteforce import detect_bruteforce
from nettriage.domain.detection.model import Severity
from nettriage.domain.flows import NetworkFlow

ATTACKER = "198.51.100.7"
SERVER = "10.0.1.20"


def attempts(
    count: int, *, port: int = 22, host: str = SERVER, start: float = 0.0, spacing: float = 5.0
) -> list[NetworkFlow]:
    return [
        make_flow(
            ATTACKER,
            host,
            port,
            at=start + n * spacing,
            src_port=50_000 + n,
            packets=12,
            bytes=2_400,
            action="ACCEPT",
        )
        for n in range(count)
    ]


def session(
    *, at: float, bytes: int = 200_000, duration: float = 30.0, port: int = 22, host: str = SERVER
) -> NetworkFlow:
    return make_flow(
        ATTACKER,
        host,
        port,
        at=at,
        duration=duration,
        src_port=60_000,
        packets=400,
        bytes=bytes,
        action="ACCEPT",
    )


def test_thirty_short_ssh_attempts_in_five_minutes_is_a_medium_finding() -> None:
    [finding] = detect_bruteforce(attempts(30, spacing=9.0))

    assert finding.detector_id == "remote_access_bruteforce"
    assert finding.severity is Severity.MEDIUM
    assert finding.src_ip == ip_address(ATTACKER)
    assert finding.dst_ip == ip_address(SERVER)
    assert finding.dst_port == 22
    assert finding.metrics["variant"] == "single"
    assert finding.metrics["possible_success"] is False
    assert finding.candidate_techniques == ("T1110", "T1110.001")
    assert (
        finding.title
        == f"SSH brute force against {SERVER} from {ATTACKER}: 30 attempts in 5 minutes"
    )


def test_twenty_nine_attempts_is_not_brute_force() -> None:
    assert detect_bruteforce(attempts(29, spacing=9.0)) == []


def test_attempts_spread_over_more_than_five_minutes_do_not_count() -> None:
    assert detect_bruteforce(attempts(40, spacing=11.0)) == []


def test_full_sessions_are_not_attempts() -> None:
    sessions = [session(at=n * 5.0, bytes=5_000, duration=2.0) for n in range(40)]
    big = [
        make_flow(ATTACKER, SERVER, 22, at=n * 5.0, packets=21, action="ACCEPT") for n in range(40)
    ]

    assert detect_bruteforce(sessions + big) == []


def test_a_large_later_session_marks_a_possible_login_as_high() -> None:
    [finding] = detect_bruteforce([*attempts(40), session(at=40 * 5.0 + 60)])

    assert finding.severity is Severity.HIGH
    assert finding.metrics["possible_success"] is True
    assert finding.metrics["success_bytes"] == 200_000
    assert finding.candidate_techniques == ("T1110", "T1110.001", "T1021.004")
    assert finding.title.endswith("(possible successful login)")


def test_a_long_later_session_also_counts() -> None:
    [finding] = detect_bruteforce([*attempts(40), session(at=400.0, bytes=9_000, duration=600.0)])

    assert finding.metrics["possible_success"] is True


def test_a_session_more_than_thirty_minutes_later_does_not_count() -> None:
    last_attempt = 39 * 5.0

    [finding] = detect_bruteforce([*attempts(40), session(at=last_attempt + 30 * 60 + 1)])

    assert finding.severity is Severity.MEDIUM


def test_rdp_uses_its_own_login_technique() -> None:
    [finding] = detect_bruteforce([*attempts(35, port=3389), session(at=300.0, port=3389)])

    assert finding.candidate_techniques == ("T1110", "T1110.001", "T1021.001")
    assert finding.title.startswith("RDP brute force")


def test_spraying_ten_hosts_is_a_spray_finding() -> None:
    flows = [
        attempt
        for n in range(12)
        for attempt in attempts(2, port=3389, host=f"10.0.4.{n + 1}", start=n * 10.0)
    ]

    [finding] = detect_bruteforce(flows)

    assert finding.metrics["variant"] == "spray"
    assert finding.dst_ip is None
    assert finding.dst_port == 3389
    assert finding.metrics["hosts"] == 12
    assert finding.candidate_techniques == ("T1110", "T1110.003")


def test_nine_hosts_is_not_spraying() -> None:
    flows = [attempt for n in range(9) for attempt in attempts(2, host=f"10.0.4.{n + 1}")]

    assert detect_bruteforce(flows) == []


def test_server_replies_from_port_22_are_not_attempts() -> None:
    replies = [
        make_flow(
            SERVER, ATTACKER, 50_000 + n, at=n * 2.0, src_port=22, packets=10, action="ACCEPT"
        )
        for n in range(100)
    ]

    assert detect_bruteforce(replies) == []
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_bruteforce.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.detection.bruteforce'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/detection/bruteforce.py`:
```python
"""`remote_access_bruteforce` v1 (spec §8.2): in any 5-minute window, one source makes at least
30 small flows (at most 20 packets each) to TCP 22 or 3389 on one host, or reaches at least 10
hosts on one of those ports (spraying). A later large or long flow from the same source to an
attacked host and port, within 30 minutes, marks a possible successful login."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

from nettriage.domain.detection.model import (
    DetectorInfo,
    Finding,
    Severity,
    fingerprint,
    flow_order,
    sample_evidence,
)
from nettriage.domain.detection.windows import Episode, sliding_episodes
from nettriage.domain.flows import TCP, IPAddress, NetworkFlow

DETECTOR_ID = "remote_access_bruteforce"
VERSION = 1
SERVICES = {22: "SSH", 3389: "RDP"}
MIN_ATTEMPTS = 30
ATTEMPT_MAX_PACKETS = 20
SPRAY_MIN_HOSTS = 10
SUCCESS_MIN_BYTES = 100 * 1024
SUCCESS_MIN_DURATION = timedelta(minutes=5)
SUCCESS_WITHIN = timedelta(minutes=30)
GUESSING_TECHNIQUES = ("T1110", "T1110.001")
SPRAYING_TECHNIQUES = ("T1110", "T1110.003")
LOGIN_TECHNIQUES = {22: "T1021.004", 3389: "T1021.001"}

type Target = tuple[IPAddress, IPAddress, int]


def _is_attempt(flow: NetworkFlow) -> bool:
    return flow.packets <= ATTEMPT_MAX_PACKETS


def _is_success(flow: NetworkFlow) -> bool:
    return flow.bytes >= SUCCESS_MIN_BYTES or flow.end - flow.start >= SUCCESS_MIN_DURATION


def detect_bruteforce(flows: Sequence[NetworkFlow]) -> list[Finding]:
    to_service: defaultdict[Target, list[NetworkFlow]] = defaultdict(list)
    for flow in flows:
        if flow.protocol == TCP and flow.dst_port in SERVICES:
            to_service[(flow.src_ip, flow.dst_ip, flow.dst_port)].append(flow)
    spray_groups: defaultdict[tuple[IPAddress, int], list[NetworkFlow]] = defaultdict(list)
    for target, group in to_service.items():
        group.sort(key=flow_order)
        spray_groups[(target[0], target[2])].extend(f for f in group if _is_attempt(f))
    successes = _SuccessIndex(to_service)

    findings: list[Finding] = []
    for (src, dst, port), group in to_service.items():
        attempts = [flow for flow in group if _is_attempt(flow)]
        for episode in sliding_episodes(
            attempts, key=lambda f: f.line_no, min_distinct=MIN_ATTEMPTS
        ):
            success = successes.first_after(episode, [(src, dst, port)])
            service = SERVICES[port]
            title = (
                f"{service} brute force against {dst} from {src}: "
                f"{episode.peak_distinct} attempts in 5 minutes"
            )
            findings.append(
                _finding(
                    "single",
                    episode,
                    src=src,
                    dst_ip=dst,
                    port=port,
                    entities=[str(dst), str(port)],
                    techniques=GUESSING_TECHNIQUES,
                    success=success,
                    title=title,
                )
            )
    for (src, port), attempts in spray_groups.items():
        attempts.sort(key=flow_order)
        for episode in sliding_episodes(
            attempts, key=lambda f: f.dst_ip, min_distinct=SPRAY_MIN_HOSTS
        ):
            hosts = sorted({flow.dst_ip for flow in episode.flows})
            success = successes.first_after(episode, [(src, host, port) for host in hosts])
            service = SERVICES[port]
            title = (
                f"{service} password spraying across {episode.peak_distinct} hosts "
                f"from {src} in 5 minutes"
            )
            findings.append(
                _finding(
                    "spray",
                    episode,
                    src=src,
                    dst_ip=None,
                    port=port,
                    entities=[str(port)],
                    techniques=SPRAYING_TECHNIQUES,
                    success=success,
                    title=title,
                )
            )
    return findings


class _SuccessIndex:
    """Large or long flows per (source, host, port), sorted by start, for the success check."""

    def __init__(self, to_service: dict[Target, list[NetworkFlow]]) -> None:
        self._flows: dict[Target, list[NetworkFlow]] = {}
        for target, group in to_service.items():
            candidates = [flow for flow in group if _is_success(flow)]
            if candidates:
                self._flows[target] = candidates

    def first_after(self, episode: Episode, targets: list[Target]) -> NetworkFlow | None:
        """The earliest success-like flow that starts after the episode's first attempt and
        no later than 30 minutes after its last attempt."""
        earliest = episode.flows[0].start
        latest = episode.flows[-1].start + SUCCESS_WITHIN
        found: list[NetworkFlow] = []
        for target in targets:
            candidates = self._flows.get(target, [])
            starts = [flow.start for flow in candidates]
            found.extend(candidates[bisect_left(starts, earliest) : bisect_right(starts, latest)])
        return min(found, key=flow_order) if found else None


def _finding(
    variant: str,
    episode: Episode,
    *,
    src: IPAddress,
    dst_ip: IPAddress | None,
    port: int,
    entities: list[str],
    techniques: tuple[str, ...],
    success: NetworkFlow | None,
    title: str,
) -> Finding:
    flows = episode.flows
    window_start = flows[0].start
    metrics: dict[str, int | float | bool | str] = {
        "variant": variant,
        "peak_distinct": episode.peak_distinct,
        "attempts": len(flows),
        "hosts": len({flow.dst_ip for flow in flows}),
        "possible_success": success is not None,
    }
    evidence = list(flows)
    if success is not None:
        metrics["success_bytes"] = success.bytes
        metrics["success_seconds"] = int((success.end - success.start).total_seconds())
        techniques = (*techniques, LOGIN_TECHNIQUES[port])
        title += " (possible successful login)"
        evidence.append(success)
    return Finding(
        detector_id=DETECTOR_ID,
        detector_version=VERSION,
        fingerprint=fingerprint(DETECTOR_ID, VERSION, [variant, str(src), *entities], window_start),
        severity=Severity.HIGH if success is not None else Severity.MEDIUM,
        title=title,
        src_ip=src,
        dst_ip=dst_ip,
        dst_port=port,
        protocol=TCP,
        window_start=window_start,
        window_end=max(flow.end for flow in evidence),
        metrics=metrics,
        candidate_techniques=techniques,
        evidence=sample_evidence(evidence),
    )


INFO = DetectorInfo(
    id=DETECTOR_ID,
    version=VERSION,
    name="SSH/RDP brute force",
    description=(
        "Many short connections to SSH (22) or RDP (3389) on one host within 5 minutes, or the "
        "same port on many hosts (password spraying). A later large or long session from the "
        "same source marks a possible successful login."
    ),
    candidate_techniques=(
        "T1110",
        "T1110.001",
        "T1110.003",
        LOGIN_TECHNIQUES[22],
        LOGIN_TECHNIQUES[3389],
    ),
    run=detect_bruteforce,
)
```

- [ ] **Step 4: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_bruteforce.py -q`
Expected: `11 passed`.
Run: `just lint test`
Expected: clean, and `137 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/domain/detection/bruteforce.py backend/tests/unit/domain/test_bruteforce.py
git commit -m "feat(domain): SSH/RDP brute-force and spraying detector

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The `outbound_volume` detector

**Files:**
- Create: `backend/src/nettriage/domain/detection/outbound_volume.py`
- Test: `backend/tests/unit/domain/test_outbound_volume.py`

**Interfaces:**
- Consumes: `is_internal` (Task 1); `DetectorInfo`, `Finding`, `Severity`, `fingerprint` and `sample_evidence` (Task 4).
- Produces: `detect_outbound_volume(flows) -> list[Finding]`, `INFO: DetectorInfo`, and `MB`.
  - `metrics` keys: `bytes`, `megabytes`, `flows`, `dominant_port`, `internal_hosts` and `robust_z_applied`, plus `robust_z` when the z-score applied.
  - Fingerprint entities: `[src, dst]`.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/domain/test_outbound_volume.py`:
```python
from ipaddress import ip_address

from flowmaker import make_flow

from nettriage.domain.detection.model import Severity
from nettriage.domain.detection.outbound_volume import MB, detect_outbound_volume
from nettriage.domain.flows import NetworkFlow

EXFIL_DST = "203.0.113.50"


def upload(
    src: str,
    dst: str,
    megabytes: float,
    *,
    port: int = 443,
    chunks: int = 10,
    action: str = "ACCEPT",
) -> list[NetworkFlow]:
    size = int(megabytes * MB / chunks)
    return [
        make_flow(
            src,
            dst,
            port,
            at=n * 30.0,
            duration=25.0,
            packets=size // 1400 + 1,
            bytes=size,
            action="ACCEPT" if action == "ACCEPT" else "REJECT",
        )
        for n in range(chunks)
    ]


def baseline(hosts: int = 20) -> list[NetworkFlow]:
    """Ordinary hosts, each sending 1-3 MB to a web service."""
    return [
        flow for n in range(hosts) for flow in upload(f"10.0.5.{n + 1}", "198.51.100.80", 1 + n % 3)
    ]


def test_a_large_outlier_upload_is_a_finding() -> None:
    [finding] = detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 80))

    assert finding.detector_id == "outbound_volume"
    assert finding.severity is Severity.MEDIUM
    assert finding.src_ip == ip_address("10.0.5.200")
    assert finding.dst_ip == ip_address(EXFIL_DST)
    assert finding.dst_port == 443
    assert finding.metrics["robust_z_applied"] is True
    assert float(finding.metrics["robust_z"]) >= 5
    assert finding.candidate_techniques == ("T1048", "T1041", "T1567")
    assert finding.title == f"80 MB sent from 10.0.5.200 to {EXFIL_DST} (mostly TCP port 443)"


def test_below_fifty_megabytes_is_never_a_finding() -> None:
    assert detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 49)) == []


def test_five_hundred_megabytes_is_high() -> None:
    [finding] = detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 520))

    assert finding.severity is Severity.HIGH


def test_a_non_web_port_is_high() -> None:
    [finding] = detect_outbound_volume(baseline() + upload("10.0.5.200", EXFIL_DST, 60, port=21))

    assert finding.severity is Severity.HIGH
    assert finding.dst_port == 21


def test_when_many_hosts_send_as_much_nothing_stands_out() -> None:
    backups = [flow for n in range(8) for flow in upload(f"10.0.6.{n + 1}", "52.95.110.1", 60 + n)]

    assert detect_outbound_volume(baseline(4) + backups) == []


def test_with_fewer_than_five_hosts_only_the_absolute_threshold_applies() -> None:
    flows = upload("10.0.5.1", EXFIL_DST, 55) + upload("10.0.5.2", "198.51.100.80", 54)

    findings = detect_outbound_volume(flows)

    assert len(findings) == 2
    assert all(f.metrics["robust_z_applied"] is False for f in findings)


def test_inbound_internal_and_rejected_traffic_do_not_count() -> None:
    inbound = upload(EXFIL_DST, "10.0.5.200", 300)
    internal = upload("10.0.5.200", "10.0.7.7", 300)
    rejected = upload("10.0.5.200", EXFIL_DST, 300, action="REJECT")

    assert detect_outbound_volume(baseline() + inbound + internal + rejected) == []
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_outbound_volume.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.detection.outbound_volume'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/detection/outbound_volume.py`:
```python
"""`outbound_volume` v1 (spec §8.2): an internal source sends at least 50 MB to one external
destination within the file, and that volume's robust z-score against every internal host's
largest outbound volume is at least 5. With fewer than 5 internal hosts, or a MAD of 0, only
the absolute threshold applies."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from statistics import median

from nettriage.domain.addresses import is_internal
from nettriage.domain.detection.model import (
    DetectorInfo,
    Finding,
    Severity,
    fingerprint,
    sample_evidence,
)
from nettriage.domain.flows import IPAddress, NetworkFlow, protocol_name

DETECTOR_ID = "outbound_volume"
VERSION = 1
MB = 1024 * 1024
MIN_BYTES = 50 * MB
HIGH_BYTES = 500 * MB
MIN_ROBUST_Z = 5.0
MIN_HOSTS_FOR_Z = 5
# Scales the MAD so it estimates the standard deviation of normally distributed data.
MAD_TO_SIGMA = 1.4826
WEB_PORTS = frozenset({80, 443})
TECHNIQUES = ("T1048", "T1041", "T1567")


def detect_outbound_volume(flows: Sequence[NetworkFlow]) -> list[Finding]:
    pairs: defaultdict[tuple[IPAddress, IPAddress], list[NetworkFlow]] = defaultdict(list)
    for flow in flows:
        if flow.action == "ACCEPT" and is_internal(flow.src_ip) and not is_internal(flow.dst_ip):
            pairs[(flow.src_ip, flow.dst_ip)].append(flow)
    totals = {pair: sum(flow.bytes for flow in group) for pair, group in pairs.items()}
    host_max: dict[IPAddress, int] = {}
    for (src, _), total in totals.items():
        host_max[src] = max(total, host_max.get(src, 0))
    baseline = _Baseline(list(host_max.values()))

    findings: list[Finding] = []
    for (src, dst), total in totals.items():
        if total < MIN_BYTES:
            continue
        z = baseline.robust_z(total)
        if z is not None and z < MIN_ROBUST_Z:
            continue
        findings.append(_finding(src, dst, pairs[(src, dst)], total, z, len(host_max)))
    return findings


class _Baseline:
    """Median and MAD of the per-host maximum outbound volumes."""

    def __init__(self, volumes: list[int]) -> None:
        self._usable = len(volumes) >= MIN_HOSTS_FOR_Z
        self._median = median(volumes) if volumes else 0.0
        self._mad = median(abs(v - self._median) for v in volumes) if volumes else 0.0

    def robust_z(self, volume: int) -> float | None:
        """None when the absolute threshold alone applies."""
        if not self._usable or self._mad == 0:
            return None
        return (volume - self._median) / (MAD_TO_SIGMA * self._mad)


def _finding(
    src: IPAddress,
    dst: IPAddress,
    flows: list[NetworkFlow],
    total: int,
    z: float | None,
    hosts: int,
) -> Finding:
    by_service: Counter[tuple[int, int]] = Counter()
    for flow in flows:
        by_service[(flow.dst_port, flow.protocol)] += flow.bytes
    (port, protocol), _ = min(by_service.items(), key=lambda item: (-item[1], item[0]))
    severity = Severity.HIGH if total >= HIGH_BYTES or port not in WEB_PORTS else Severity.MEDIUM
    window_start = min(flow.start for flow in flows)
    metrics: dict[str, int | float | bool | str] = {
        "bytes": total,
        "megabytes": round(total / MB, 1),
        "flows": len(flows),
        "dominant_port": port,
        "internal_hosts": hosts,
        "robust_z_applied": z is not None,
    }
    if z is not None:
        metrics["robust_z"] = round(z, 2)
    return Finding(
        detector_id=DETECTOR_ID,
        detector_version=VERSION,
        fingerprint=fingerprint(DETECTOR_ID, VERSION, [str(src), str(dst)], window_start),
        severity=severity,
        title=(
            f"{total / MB:,.0f} MB sent from {src} to {dst} "
            f"(mostly {protocol_name(protocol)} port {port})"
        ),
        src_ip=src,
        dst_ip=dst,
        dst_port=port,
        protocol=protocol,
        window_start=window_start,
        window_end=max(flow.end for flow in flows),
        metrics=metrics,
        candidate_techniques=TECHNIQUES,
        evidence=sample_evidence(flows),
    )


INFO = DetectorInfo(
    id=DETECTOR_ID,
    version=VERSION,
    name="Unusual outbound volume",
    description=(
        "An internal host sending at least 50 MB to one external destination, far more than "
        "the other hosts in the file send (robust z-score of 5 or more)."
    ),
    candidate_techniques=TECHNIQUES,
    run=detect_outbound_volume,
)
```

- [ ] **Step 4: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_outbound_volume.py -q`
Expected: `7 passed`.
Run: `just lint test`
Expected: clean, and `144 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/nettriage/domain/detection/outbound_volume.py backend/tests/unit/domain/test_outbound_volume.py
git commit -m "feat(domain): unusual outbound volume detector with a robust z-score

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The detection engine and the coverage gate

**Files:**
- Create: `backend/src/nettriage/domain/detection/engine.py`
- Test: `backend/tests/unit/domain/test_detection_engine.py`
- Modify: `justfile` (the `test` recipe), `.github/workflows/ci.yml` (the `backend` job's test command)

**Interfaces:**
- Consumes: `INFO` from Tasks 5–7; `rank_and_cap` and `MAX_FINDINGS_PER_UPLOAD` (Task 4); `parse_flow_log` (Task 3, used in a test).
- Produces, in `nettriage.domain.detection.engine`:
  - `DETECTORS: tuple[DetectorInfo, ...]`, in the order port_scan, remote_access_bruteforce, outbound_volume. Plan 3 syncs this into the `detectors` table.
  - `DetectionResult(findings: list[Finding], truncated: int)`.
  - `detect(flows, max_findings=50) -> DetectionResult`. Plan 4's `analyze` worker calls it.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/domain/test_detection_engine.py`:
```python
import io
import random
import time

from flowmaker import make_flow

from nettriage.domain.detection.engine import DETECTORS, detect
from nettriage.domain.detection.model import Severity
from nettriage.domain.flows import NetworkFlow
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log


def scan(src: str, ports: int) -> list[NetworkFlow]:
    return [make_flow(src, "10.0.1.20", 1 + n, at=n) for n in range(ports)]


def test_every_detector_is_registered_with_reference_data() -> None:
    assert [d.id for d in DETECTORS] == ["port_scan", "remote_access_bruteforce", "outbound_volume"]
    assert all(d.version == 1 and d.name and d.description for d in DETECTORS)
    assert "T1046" in DETECTORS[0].candidate_techniques


def test_findings_are_capped_at_fifty_highest_severity_first() -> None:
    external = [flow for n in range(60) for flow in scan(f"198.51.100.{n + 1}", 110)]
    internal = scan("10.0.9.9", 110)

    result = detect(external + internal)

    assert len(result.findings) == 50
    assert result.truncated == 11
    assert result.findings[0].severity is Severity.HIGH
    assert str(result.findings[0].src_ip) == "10.0.9.9"


def test_the_same_file_always_gives_the_same_findings() -> None:
    lines = [
        f"2 123456789012 eni-1 198.51.100.7 10.0.1.20 40000 {port} 6 1 60 "
        f"{1_790_000_000 + port} {1_790_000_001 + port} REJECT OK"
        for port in range(1, 200)
    ]
    data = ("\n".join(lines) + "\n").encode()

    first = detect(parse_flow_log(io.BytesIO(data)).flows)
    second = detect(parse_flow_log(io.BytesIO(data)).flows)

    assert first == second
    assert [f.fingerprint for f in first.findings] == [f.fingerprint for f in second.findings]


def test_a_hundred_thousand_flows_are_detected_in_seconds() -> None:
    rng = random.Random(7)
    flows = [
        make_flow(
            f"10.0.{rng.randrange(4)}.{rng.randrange(1, 250)}",
            f"198.51.{rng.randrange(100)}.{rng.randrange(1, 250)}",
            rng.choice([443, 443, 80, 53, 22, 3389, 5432]),
            at=rng.uniform(0, 3600),
            packets=rng.randrange(1, 50),
            bytes=rng.randrange(60, 200_000),
            action=rng.choice(["ACCEPT", "ACCEPT", "REJECT"]),
        )
        for _ in range(100_000)
    ]

    started = time.perf_counter()
    detect(flows)

    assert time.perf_counter() - started < 15
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_detection_engine.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'nettriage.domain.detection.engine'`.

- [ ] **Step 3: Implement**

`backend/src/nettriage/domain/detection/engine.py`:
```python
"""Run every detector over one upload's flows (spec §8.2, §5 per-upload limits)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from nettriage.domain.detection import bruteforce, outbound_volume, port_scan
from nettriage.domain.detection.model import (
    MAX_FINDINGS_PER_UPLOAD,
    DetectorInfo,
    Finding,
    rank_and_cap,
)
from nettriage.domain.flows import NetworkFlow

DETECTORS: tuple[DetectorInfo, ...] = (port_scan.INFO, bruteforce.INFO, outbound_volume.INFO)


@dataclass(frozen=True)
class DetectionResult:
    """At most 50 findings, highest severity first; `truncated` counts the rest."""

    findings: list[Finding]
    truncated: int


def detect(
    flows: Sequence[NetworkFlow], max_findings: int = MAX_FINDINGS_PER_UPLOAD
) -> DetectionResult:
    candidates = [finding for detector in DETECTORS for finding in detector.run(flows)]
    kept, truncated = rank_and_cap(candidates, max_findings)
    return DetectionResult(findings=kept, truncated=truncated)
```

- [ ] **Step 4: Gate coverage of the domain at 85%**

In `justfile`, replace the `test` recipe's command:
```
    cd backend && uv run python -m pytest
```
with:
```
    cd backend && uv run python -m pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85
```

In `.github/workflows/ci.yml`, in the `backend` job's "Lint, type-check and test" step, replace the line `uv run --locked pytest` with:
```
          uv run --locked pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85
```

- [ ] **Step 5: Run the tests and checks**

Run: `cd backend && uv run python -m pytest tests/unit/domain/test_detection_engine.py -q`
Expected: `4 passed`. The performance test detects 100,000 flows in a few seconds, well under its 15-second limit.
Run: `just lint test`
Expected:
- lint is clean;
- `148 passed`;
- the coverage table shows `nettriage/domain` at about 99%, and ends with `Required test coverage of 85% reached`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/nettriage/domain/detection/engine.py backend/tests/unit/domain/test_detection_engine.py justfile .github/workflows/ci.yml
git commit -m "feat(domain): run every detector, rank and cap findings; gate domain coverage at 85%

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Synthetic, labeled scenarios

**Files:**
- Create: `tools/scenarios/__init__.py`, `tools/scenarios/traffic.py`, `tools/scenarios/catalog.py`
- Test: `tools/tests/test_scenarios_traffic.py`

**Interfaces:**
- Consumes: `parse_flow_log` (Task 3) and `detect` (Task 8), both in tests only.
- Produces:
  - In `tools.scenarios.traffic`:
    - `Label(detector_id, src_ip, dst_ip=None, dst_port=None)`;
    - `Scenario(name, seed, records, labels)`, whose `.text()` returns a flow log with a header;
    - `Traffic(seed)`, with `background()`, `vertical_scan`, `horizontal_scan`, `login_attempts`, `login_session`, `upload`, `external_ip()`, `ephemeral_port()`, `flow()` and `scenario(name)`.
  - In `tools.scenarios.catalog`:
    - `FAMILIES: dict[str, Family]`: 10 attack families and 7 near-miss families;
    - `build(name, seed) -> Scenario`;
    - `suite(seeds) -> list[Scenario]`.

The same seed always produces the same file. Every scenario sits on an hour of benign background traffic. That background includes servers answering clients, DNS replies, internet noise, admin SSH sessions, a monitoring host checking 8 servers on port 22, and backups of 15–40 MB, and it must never produce a finding.

- [ ] **Step 1: Write the failing test**

`tools/tests/test_scenarios_traffic.py`:
```python
import io
from ipaddress import ip_address

import pytest
from nettriage.domain.addresses import is_internal
from nettriage.domain.detection.engine import detect
from nettriage.domain.flows import NetworkFlow
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

from tools.scenarios.catalog import FAMILIES, build
from tools.scenarios.traffic import Traffic

ATTACK_FAMILIES = {
    "external_vertical_scan",
    "internal_horizontal_scan",
    "scan_across_a_window_boundary",
    "udp_scan",
    "ssh_brute_force",
    "brute_force_then_login",
    "rdp_password_spraying",
    "exfiltration_over_https",
    "exfiltration_over_another_port",
    "everything_at_once",
}


def flows_of(text: str) -> list[NetworkFlow]:
    return parse_flow_log(io.BytesIO(text.encode())).flows


def test_the_same_seed_always_writes_the_same_file() -> None:
    assert build("udp_scan", 4).text() == build("udp_scan", 4).text()
    assert build("udp_scan", 4).text() != build("udp_scan", 5).text()


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_scenario_is_a_clean_flow_log(family: str) -> None:
    result = parse_flow_log(io.BytesIO(build(family, 1).text().encode()))

    assert result.has_header
    assert result.rows_rejected == 0
    assert result.rows_parsed > 1_000


def test_attack_families_are_labeled_and_near_misses_are_not() -> None:
    for family in FAMILIES:
        assert bool(build(family, 1).labels) is (family in ATTACK_FAMILIES), family


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_background_traffic_alone_produces_no_findings(seed: int) -> None:
    traffic = Traffic(seed)
    traffic.background()

    assert detect(flows_of(traffic.scenario("background").text())).findings == []


def test_external_addresses_are_never_internal() -> None:
    traffic = Traffic(9)

    assert not any(is_internal(ip_address(traffic.external_ip())) for _ in range(2_000))
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --project backend python -m pytest tools/tests/test_scenarios_traffic.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.scenarios'`.

- [ ] **Step 3: Implement**

`tools/scenarios/__init__.py`:
```python
"""Seeded, labeled synthetic flow logs that measure the detectors (spec §8.2 "Quality
measurement"). The numbers guard against regressions; they don't measure real-world accuracy."""
```

`tools/scenarios/traffic.py`:
```python
"""Building blocks for synthetic VPC Flow Logs: one `Traffic` per scenario, seeded so the same
seed always writes the same file."""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field

# 2026-09-01T12:00:00Z; every scenario covers the hour after it.
EPOCH = 1_788_264_000
HOUR = 3_600
ACCOUNT = "123456789012"
HEADER = (
    "version account-id interface-id srcaddr dstaddr srcport dstport protocol packets bytes "
    "start end action log-status"
)
TCP = 6
UDP = 17
MB = 1024 * 1024
RESOLVER = "10.20.0.2"
WEB_SERVER = "10.20.2.10"
BACKUP_SERVICE = "52.95.110.1"


@dataclass(frozen=True)
class Label:
    """An attack the suite expects a finding for. `None` fields match anything."""

    detector_id: str
    src_ip: str
    dst_ip: str | None = None
    dst_port: int | None = None


@dataclass
class Scenario:
    name: str
    seed: int
    records: list[str]
    labels: list[Label]

    def text(self) -> str:
        return "\n".join([HEADER, *self.records]) + "\n"


@dataclass
class Traffic:
    """Collects one scenario's flow records; `rng` drives every random choice."""

    seed: int
    rng: random.Random = field(init=False)
    internal_hosts: list[str] = field(init=False)
    labels: list[Label] = field(default_factory=list)
    _records: list[tuple[int, int, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        count = self.rng.randint(15, 35)
        self.internal_hosts = [f"10.20.1.{n}" for n in range(10, 10 + count)]

    def flow(
        self,
        src: str,
        dst: str,
        src_port: int,
        dst_port: int,
        *,
        at: int,
        protocol: int = TCP,
        packets: int = 1,
        bytes_: int = 60,
        duration: int = 1,
        action: str = "ACCEPT",
    ) -> None:
        interface = f"eni-{zlib.crc32(src.encode()):08x}"
        record = (
            f"2 {ACCOUNT} {interface} {src} {dst} {src_port} {dst_port} {protocol} {packets} "
            f"{bytes_} {EPOCH + at} {EPOCH + at + duration} {action} OK"
        )
        self._records.append((at, len(self._records), record))

    def external_ip(self) -> str:
        """A random public address, never inside the internal ranges."""
        first = self.rng.choice([3, 13, 18, 23, 34, 44, 51, 54, 81, 91, 104, 142, 185, 203])
        second, third = self.rng.randrange(256), self.rng.randrange(256)
        return f"{first}.{second}.{third}.{self.rng.randrange(1, 255)}"

    def ephemeral_port(self) -> int:
        return self.rng.randint(32_768, 60_999)

    def scenario(self, name: str) -> Scenario:
        ordered = [record for _, _, record in sorted(self._records)]
        return Scenario(name=name, seed=self.seed, records=ordered, labels=list(self.labels))

    # Benign background ------------------------------------------------------------------

    def background(self) -> None:
        """An hour of ordinary traffic that must never produce a finding."""
        rng = self.rng
        for host in self.internal_hosts:
            for _ in range(rng.randint(20, 60)):
                self._web_request(host, self.external_ip(), at=rng.randrange(HOUR))
            for _ in range(rng.randint(10, 40)):
                at = rng.randrange(HOUR)
                port = self.ephemeral_port()
                self.flow(host, RESOLVER, port, 53, at=at, protocol=UDP, bytes_=70)
                self.flow(RESOLVER, host, 53, port, at=at, protocol=UDP, bytes_=180)
        for _ in range(rng.randint(150, 400)):
            self._web_request(self.external_ip(), WEB_SERVER, at=rng.randrange(HOUR))
        for _ in range(rng.randint(30, 80)):
            # Internet background noise: a few probes per address, all refused.
            prober = self.external_ip()
            at = rng.randrange(HOUR)
            for port in rng.sample([22, 23, 80, 443, 445, 3389, 8080], rng.randint(1, 4)):
                self.flow(prober, WEB_SERVER, self.ephemeral_port(), port, at=at, action="REJECT")
        admin = self.internal_hosts[0]
        for server in rng.sample(self.internal_hosts[1:], 5):
            at = rng.randrange(HOUR - 900)
            self.flow(
                admin,
                server,
                self.ephemeral_port(),
                22,
                at=at,
                packets=rng.randint(200, 900),
                bytes_=rng.randint(40_000, 900_000),
                duration=rng.randint(120, 900),
            )
        monitor = self.internal_hosts[1]
        for server in self.internal_hosts[2:10]:
            for minute in range(0, HOUR, 60):
                self.flow(
                    monitor,
                    server,
                    self.ephemeral_port(),
                    22,
                    at=minute + rng.randrange(5),
                    packets=6,
                    bytes_=900,
                )
        for host in rng.sample(self.internal_hosts, 3):
            self.upload(host, BACKUP_SERVICE, rng.uniform(15, 40), at=rng.randrange(HOUR - 1_200))

    def _web_request(self, client: str, server: str, *, at: int) -> None:
        rng = self.rng
        port = self.ephemeral_port()
        response = rng.randint(2_000, 2 * MB)
        self.flow(
            client,
            server,
            port,
            443,
            at=at,
            packets=rng.randint(4, 20),
            bytes_=rng.randint(500, 4_000),
            duration=rng.randint(1, 20),
        )
        self.flow(
            server,
            client,
            443,
            port,
            at=at,
            packets=response // 1_400 + 2,
            bytes_=response,
            duration=rng.randint(1, 20),
        )

    # Attacks and near misses --------------------------------------------------------------

    def vertical_scan(
        self,
        src: str,
        dst: str,
        ports: int,
        *,
        at: int,
        seconds: int,
        reject_percent: int = 95,
        protocol: int = TCP,
        completed: bool = False,
    ) -> None:
        for n, port in enumerate(sorted(self.rng.sample(range(1, 65_536), ports))):
            self._probe(
                src,
                dst,
                port,
                at=at + n * seconds // ports,
                protocol=protocol,
                reject_percent=reject_percent,
                completed=completed,
            )

    def horizontal_scan(
        self,
        src: str,
        hosts: list[str],
        port: int,
        *,
        at: int,
        seconds: int,
    ) -> None:
        for n, host in enumerate(hosts):
            self._probe(
                src,
                host,
                port,
                at=at + n * seconds // len(hosts),
                protocol=TCP,
                reject_percent=90,
                completed=False,
            )

    def _probe(
        self,
        src: str,
        dst: str,
        port: int,
        *,
        at: int,
        protocol: int,
        reject_percent: int,
        completed: bool,
    ) -> None:
        if completed:
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at,
                protocol=protocol,
                packets=self.rng.randint(20, 60),
                bytes_=self.rng.randint(4_000, 40_000),
            )
        elif self.rng.randrange(100) < reject_percent:
            self.flow(
                src, dst, self.ephemeral_port(), port, at=at, protocol=protocol, action="REJECT"
            )
        else:
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at,
                protocol=protocol,
                packets=self.rng.randint(1, 3),
                bytes_=120,
            )

    def login_attempts(
        self, src: str, dst: str, port: int, attempts: int, *, at: int, seconds: int
    ) -> None:
        for n in range(attempts):
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at + n * seconds // attempts,
                packets=self.rng.randint(8, 20),
                bytes_=self.rng.randint(1_500, 5_000),
                duration=self.rng.randint(1, 4),
            )

    def login_session(self, src: str, dst: str, port: int, *, at: int) -> None:
        self.flow(
            src,
            dst,
            self.ephemeral_port(),
            port,
            at=at,
            packets=self.rng.randint(300, 2_000),
            bytes_=self.rng.randint(150_000, 3 * MB),
            duration=self.rng.randint(60, 900),
        )

    def upload(self, src: str, dst: str, megabytes: float, *, at: int, port: int = 443) -> None:
        chunks = self.rng.randint(8, 30)
        size = int(megabytes * MB / chunks)
        for n in range(chunks):
            self.flow(
                src,
                dst,
                self.ephemeral_port(),
                port,
                at=at + n * 40,
                packets=size // 1_400 + 1,
                bytes_=size,
                duration=35,
            )
```

`tools/scenarios/catalog.py`:
```python
"""The generated suite: attack families that must be found, and near misses that must not.
Every scenario runs on top of an hour of benign background traffic."""

from __future__ import annotations

from collections.abc import Callable

from tools.scenarios.traffic import HOUR, UDP, WEB_SERVER, Label, Scenario, Traffic

type Family = Callable[[Traffic], None]


# Attacks ----------------------------------------------------------------------------------


def external_vertical_scan(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice([WEB_SERVER, *t.internal_hosts])
    t.vertical_scan(
        attacker,
        target,
        t.rng.randint(150, 1_500),
        at=t.rng.randrange(HOUR - 300),
        seconds=t.rng.randint(30, 240),
    )
    t.labels.append(Label("port_scan", attacker, dst_ip=target))


def internal_horizontal_scan(t: Traffic) -> None:
    compromised = t.rng.choice(t.internal_hosts)
    hosts = [f"10.20.{3 + n // 250}.{1 + n % 250}" for n in range(t.rng.randint(60, 200))]
    port = t.rng.choice([445, 3306, 5985, 6379])
    t.horizontal_scan(
        compromised, hosts, port, at=t.rng.randrange(HOUR - 300), seconds=t.rng.randint(20, 240)
    )
    t.labels.append(Label("port_scan", compromised, dst_port=port))


def scan_across_a_window_boundary(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice(t.internal_hosts)
    boundary = 300 * t.rng.randint(1, 10)
    t.vertical_scan(attacker, target, t.rng.randint(110, 140), at=boundary - 100, seconds=190)
    t.labels.append(Label("port_scan", attacker, dst_ip=target))


def udp_scan(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice(t.internal_hosts)
    t.vertical_scan(
        attacker,
        target,
        t.rng.randint(120, 400),
        at=t.rng.randrange(HOUR - 300),
        seconds=t.rng.randint(60, 240),
        protocol=UDP,
    )
    t.labels.append(Label("port_scan", attacker, dst_ip=target))


def ssh_brute_force(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice([WEB_SERVER, *t.internal_hosts])
    t.login_attempts(
        attacker,
        target,
        22,
        t.rng.randint(40, 300),
        at=t.rng.randrange(HOUR - 300),
        seconds=t.rng.randint(60, 240),
    )
    t.labels.append(Label("remote_access_bruteforce", attacker, dst_ip=target, dst_port=22))


def brute_force_then_login(t: Traffic) -> None:
    attacker, target = t.external_ip(), t.rng.choice(t.internal_hosts)
    port = t.rng.choice([22, 3389])
    start = t.rng.randrange(HOUR - 1_200)
    t.login_attempts(attacker, target, port, t.rng.randint(40, 120), at=start, seconds=200)
    t.login_session(attacker, target, port, at=start + 200 + t.rng.randint(10, 1_200))
    t.labels.append(Label("remote_access_bruteforce", attacker, dst_ip=target, dst_port=port))


def rdp_password_spraying(t: Traffic) -> None:
    attacker = t.external_ip()
    start = t.rng.randrange(HOUR - 300)
    targets = t.rng.sample(t.internal_hosts, min(len(t.internal_hosts), t.rng.randint(12, 30)))
    for n, host in enumerate(targets):
        t.login_attempts(attacker, host, 3389, t.rng.randint(1, 3), at=start + n * 5, seconds=5)
    t.labels.append(Label("remote_access_bruteforce", attacker, dst_port=3389))


def exfiltration_over_https(t: Traffic) -> None:
    source, destination = t.rng.choice(t.internal_hosts), t.external_ip()
    t.upload(source, destination, t.rng.uniform(80, 700), at=t.rng.randrange(HOUR - 1_200))
    t.labels.append(Label("outbound_volume", source, dst_ip=destination))


def exfiltration_over_another_port(t: Traffic) -> None:
    source, destination = t.rng.choice(t.internal_hosts), t.external_ip()
    t.upload(
        source,
        destination,
        t.rng.uniform(55, 150),
        at=t.rng.randrange(HOUR - 1_200),
        port=t.rng.choice([21, 22, 8080, 53]),
    )
    t.labels.append(Label("outbound_volume", source, dst_ip=destination))


def everything_at_once(t: Traffic) -> None:
    external_vertical_scan(t)
    ssh_brute_force(t)
    exfiltration_over_https(t)


# Near misses: close to a rule, and must stay quiet ----------------------------------------


def slow_scan(t: Traffic) -> None:
    t.vertical_scan(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        t.rng.randint(300, 600),
        at=0,
        seconds=HOUR - 60,
    )


def partial_scan(t: Traffic) -> None:
    t.vertical_scan(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        t.rng.randint(60, 95),
        at=t.rng.randrange(HOUR - 300),
        seconds=120,
    )


def completed_service_checks(t: Traffic) -> None:
    t.vertical_scan(
        t.rng.choice(t.internal_hosts),
        t.rng.choice(t.internal_hosts),
        150,
        at=t.rng.randrange(HOUR - 300),
        seconds=200,
        completed=True,
    )


def a_few_failed_logins(t: Traffic) -> None:
    t.login_attempts(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        22,
        t.rng.randint(10, 28),
        at=t.rng.randrange(HOUR - 300),
        seconds=240,
    )


def spraying_nine_hosts(t: Traffic) -> None:
    attacker, start = t.external_ip(), t.rng.randrange(HOUR - 300)
    for n, host in enumerate(t.rng.sample(t.internal_hosts, 9)):
        t.login_attempts(attacker, host, 3389, 2, at=start + n * 10, seconds=5)


def upload_below_the_threshold(t: Traffic) -> None:
    t.upload(
        t.rng.choice(t.internal_hosts),
        t.external_ip(),
        t.rng.uniform(30, 48),
        at=t.rng.randrange(HOUR - 1_200),
    )


def large_download(t: Traffic) -> None:
    t.upload(
        t.external_ip(),
        t.rng.choice(t.internal_hosts),
        t.rng.uniform(200, 600),
        at=t.rng.randrange(HOUR - 1_200),
    )


FAMILIES: dict[str, Family] = {
    family.__name__: family
    for family in (
        external_vertical_scan,
        internal_horizontal_scan,
        scan_across_a_window_boundary,
        udp_scan,
        ssh_brute_force,
        brute_force_then_login,
        rdp_password_spraying,
        exfiltration_over_https,
        exfiltration_over_another_port,
        everything_at_once,
        slow_scan,
        partial_scan,
        completed_service_checks,
        a_few_failed_logins,
        spraying_nine_hosts,
        upload_below_the_threshold,
        large_download,
    )
}


def build(name: str, seed: int) -> Scenario:
    traffic = Traffic(seed)
    traffic.background()
    FAMILIES[name](traffic)
    return traffic.scenario(name)


def suite(seeds: int) -> list[Scenario]:
    """Every family with seeds 1..`seeds`."""
    return [build(name, seed) for name in FAMILIES for seed in range(1, seeds + 1)]
```

- [ ] **Step 4: Run the tests**

Run: `uv run --project backend python -m pytest tools/tests/test_scenarios_traffic.py -q`
Expected: `23 passed`.
Run: `just tools-test`
Expected: `185 passed` (162 existing + 23).

- [ ] **Step 5: Commit**

```bash
git add tools/scenarios tools/tests/test_scenarios_traffic.py
git commit -m "feat(tools): seeded, labeled flow log scenarios with benign background traffic

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The detection quality report, in CI

**Files:**
- Create: `tools/scenarios/evaluate.py`, `tools/scenarios/__main__.py`
- Test: `tools/tests/test_scenarios_evaluate.py`
- Modify: `justfile` (a new `detection-report` recipe), `.github/workflows/ci.yml` (the `package` job), `README.md` (the Develop section)

**Interfaces:**
- Consumes: `FAMILIES`, `build` and `suite` (Task 9); `DETECTORS` and `detect` (Task 8); `parse_flow_log` (Task 3); `Finding` (Task 4).
- Produces:
  - In `tools.scenarios.evaluate`:
    - `TARGET = 0.9`;
    - `matches(finding, label) -> bool`;
    - `Score`, with `.precision`, `.recall` and `.passed`;
    - `score(scenarios) -> dict[str, Score]`;
    - `report(scores, scenarios, flows) -> str`, which returns Markdown.
  - The CLI `python -m tools.scenarios report [--seeds 3] [--out PATH]`, which exits 1 when any detector misses the target. The same CLI also has `write FAMILY --seed N --out PATH [--gzip]` and `list`.

- [ ] **Step 1: Write the failing test**

`tools/tests/test_scenarios_evaluate.py`:
```python
import gzip
import io
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

import pytest
from nettriage.domain.detection.model import Finding, Severity
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

from tools.scenarios.__main__ import main
from tools.scenarios.evaluate import Score, matches, report
from tools.scenarios.traffic import Label

NOON = datetime(2026, 9, 1, 12, tzinfo=UTC)


def finding(detector_id: str, src: str, dst: str | None = None, port: int | None = None) -> Finding:
    return Finding(
        detector_id=detector_id,
        detector_version=1,
        fingerprint="f",
        severity=Severity.MEDIUM,
        title="t",
        src_ip=ip_address(src),
        dst_ip=ip_address(dst) if dst else None,
        dst_port=port,
        protocol=6,
        window_start=NOON,
        window_end=NOON,
        metrics={},
        candidate_techniques=(),
        evidence=(),
    )


def test_a_label_matches_on_detector_source_and_the_fields_it_sets() -> None:
    label = Label("port_scan", "1.2.3.4", dst_port=445)

    assert matches(finding("port_scan", "1.2.3.4", port=445), label)
    assert matches(finding("port_scan", "1.2.3.4", dst="10.0.0.1", port=445), label)
    assert not matches(finding("port_scan", "1.2.3.4", port=22), label)
    assert not matches(finding("port_scan", "1.2.3.5", port=445), label)
    assert not matches(finding("outbound_volume", "1.2.3.4", port=445), label)


def test_scores_turn_counts_into_precision_and_recall() -> None:
    score = Score(true_positives=9, false_positives=1, attacks=10, detected=9)

    assert score.precision == pytest.approx(0.9)
    assert score.recall == pytest.approx(0.9)
    assert score.passed
    assert not Score(true_positives=8, false_positives=2, attacks=10, detected=10).passed
    assert Score().precision == Score().recall == 1.0


def test_the_report_lists_every_detector_and_every_note() -> None:
    scores = {
        "port_scan": Score(true_positives=3, attacks=3, detected=3),
        "outbound_volume": Score(false_positives=1, notes=["false positive in x (seed 1): y"]),
    }

    text = report(scores, scenarios=2, flows=1_234)

    assert "2 scenarios, 1,234 flows" in text
    assert "| `port_scan` | 3 | 0 | 3 | 3 | 1.00 | 1.00 | PASS |" in text
    assert "| `outbound_volume` | 1 | 1 | 0 | 0 | 0.00 | 1.00 | FAIL |" in text
    assert "- false positive in x (seed 1): y" in text


def test_report_command_scores_the_suite_and_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "report.md"

    assert main(["report", "--seeds", "1", "--out", str(out)]) == 0
    assert "| `remote_access_bruteforce` |" in out.read_text(encoding="utf-8")


def test_write_command_writes_a_gzip_flow_log(tmp_path: Path) -> None:
    out = tmp_path / "scan.log.gz"

    assert (
        main(["write", "external_vertical_scan", "--seed", "2", "--out", str(out), "--gzip"]) == 0
    )
    assert parse_flow_log(io.BytesIO(out.read_bytes())).rows_parsed > 1_000
    assert gzip.decompress(out.read_bytes()).decode().startswith("version account-id")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --project backend python -m pytest tools/tests/test_scenarios_evaluate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.scenarios.__main__'` (or `tools.scenarios.evaluate`).

- [ ] **Step 3: Implement**

`tools/scenarios/evaluate.py`:
```python
"""Run the detectors on scenarios and score them against their labels."""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass, field

from nettriage.domain.detection.engine import DETECTORS, detect
from nettriage.domain.detection.model import Finding
from nettriage.domain.parsing.vpc_flow_logs import parse_flow_log

from tools.scenarios.traffic import Label, Scenario

TARGET = 0.9


def matches(finding: Finding, label: Label) -> bool:
    return (
        finding.detector_id == label.detector_id
        and str(finding.src_ip) == label.src_ip
        and (label.dst_ip is None or str(finding.dst_ip) == label.dst_ip)
        and (label.dst_port is None or finding.dst_port == label.dst_port)
    )


@dataclass
class Score:
    """One detector's counts. Several findings for one attack are all true positives."""

    true_positives: int = 0
    false_positives: int = 0
    attacks: int = 0
    detected: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        findings = self.true_positives + self.false_positives
        return self.true_positives / findings if findings else 1.0

    @property
    def recall(self) -> float:
        return self.detected / self.attacks if self.attacks else 1.0

    @property
    def passed(self) -> bool:
        return self.precision >= TARGET and self.recall >= TARGET


def score(scenarios: Iterable[Scenario]) -> dict[str, Score]:
    scores = {detector.id: Score() for detector in DETECTORS}
    for scenario in scenarios:
        flows = parse_flow_log(io.BytesIO(scenario.text().encode())).flows
        findings = detect(flows).findings
        where = f"{scenario.name} (seed {scenario.seed})"
        for finding in findings:
            detector = scores[finding.detector_id]
            if any(matches(finding, label) for label in scenario.labels):
                detector.true_positives += 1
            else:
                detector.false_positives += 1
                detector.notes.append(f"false positive in {where}: {finding.title}")
        for label in scenario.labels:
            detector = scores[label.detector_id]
            detector.attacks += 1
            if any(matches(finding, label) for finding in findings):
                detector.detected += 1
            else:
                detector.notes.append(f"missed in {where}: {label}")
    return scores


def report(scores: dict[str, Score], scenarios: int, flows: int) -> str:
    lines = [
        "# Detection quality report",
        "",
        f"Generated suite: {scenarios} scenarios, {flows:,} flows. "
        f"Target per detector: precision and recall of at least {TARGET:.2f}.",
        "Synthetic data guards against regressions; it doesn't measure real-world accuracy.",
        "",
        "| Detector | Findings | False positives | Attacks | Detected | Precision | Recall "
        "| Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for detector_id, s in scores.items():
        lines.append(
            f"| `{detector_id}` | {s.true_positives + s.false_positives} | {s.false_positives} "
            f"| {s.attacks} | {s.detected} | {s.precision:.2f} | {s.recall:.2f} "
            f"| {'PASS' if s.passed else 'FAIL'} |"
        )
    notes = [note for s in scores.values() for note in s.notes]
    if notes:
        lines += ["", "## False positives and misses", "", *(f"- {note}" for note in notes)]
    return "\n".join(lines) + "\n"
```

`tools/scenarios/__main__.py`:
```python
"""`python -m tools.scenarios report|write|list`: measure the detectors or write one scenario."""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

from tools.scenarios.catalog import FAMILIES, build, suite
from tools.scenarios.evaluate import report, score

DEFAULT_SEEDS = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.scenarios")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("report", help="score every detector on the generated suite")
    run.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    run.add_argument("--out", type=Path, help="also write the Markdown report here")
    write = commands.add_parser("write", help="write one scenario as a flow log file")
    write.add_argument("family", choices=sorted(FAMILIES))
    write.add_argument("--seed", type=int, default=1)
    write.add_argument("--out", type=Path, required=True)
    write.add_argument("--gzip", action="store_true")
    commands.add_parser("list", help="list the scenario families")
    args = parser.parse_args(argv)

    if args.command == "list":
        print("\n".join(sorted(FAMILIES)))
        return 0
    if args.command == "write":
        data = build(args.family, args.seed).text().encode()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(gzip.compress(data, mtime=0) if args.gzip else data)
        print(f"Wrote {args.out}")
        return 0

    scenarios = suite(args.seeds)
    scores = score(scenarios)
    text = report(scores, len(scenarios), sum(len(s.records) for s in scenarios))
    print(text, end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0 if all(s.passed for s in scores.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Publish the report from CI**

In `.github/workflows/ci.yml`, in the `package` job, insert these two steps directly after the "Test the tools" step:
```yaml
      - name: Measure detection quality
        run: |
          status=0
          uv run --locked --project backend python -m tools.scenarios report --out dist/detection-report.md || status=$?
          cat dist/detection-report.md >> "$GITHUB_STEP_SUMMARY"
          exit $status
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        if: always()
        with:
          name: detection-report
          path: dist/detection-report.md
          retention-days: 7
```
The upload action uses the same pinned SHA as the job's existing `backend-zip` upload.

In `justfile`, add this after the `tools-test` recipe:
```
# Detection quality: precision and recall of every detector on the generated scenarios
detection-report:
    uv run --project backend python -m tools.scenarios report --out dist/detection-report.md
```

In `README.md`, add this paragraph directly after the Develop section's code block (before `## Deploy`):
```markdown
Detector quality: `just detection-report` scores every detector's precision and recall on a
seeded, synthetic scenario suite (target: 0.90 or more for both). CI publishes the report on
every run as the `detection-report` artifact and in the job summary.
```

- [ ] **Step 5: Run the tests, the report and the checks**

Run: `uv run --project backend python -m pytest tools/tests/test_scenarios_evaluate.py -q`
Expected: `5 passed`.

Run: `just detection-report`
Expected: the table below, then exit code 0. The suite covers 51 scenarios and 198,449 flows, and the run takes about 10–15 seconds.
```
| `port_scan` | 15 | 0 | 15 | 15 | 1.00 | 1.00 | PASS |
| `remote_access_bruteforce` | 12 | 0 | 12 | 12 | 1.00 | 1.00 | PASS |
| `outbound_volume` | 9 | 0 | 9 | 9 | 1.00 | 1.00 | PASS |
```

Run: `just tools-test pin-check cloud-check`
Expected: `190 passed`, every action is pinned, and CI holds no cloud access.

- [ ] **Step 6: Commit**

```bash
git add tools/scenarios/evaluate.py tools/scenarios/__main__.py tools/tests/test_scenarios_evaluate.py justfile .github/workflows/ci.yml README.md
git commit -m "feat(tools): detection quality report with precision and recall, published by CI

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11 (Claude, then the owner): Pull request and review

- [ ] **Step 1 (Claude):** Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report`. Everything must pass: backend `148 passed` with domain coverage ≥ 85%, and tools `190 passed`.
- [ ] **Step 2 (Claude):** Push `plan-2/detection-engine`. Open a PR against `main` whose description links this plan and pastes the detection report table, then run `gh pr checks --watch`. Expected: every required check and CodeQL pass, and the `package` job's summary shows the report.
- [ ] **Step 3 (Claude):** Run the review loop (spec §11.6): request a Copilot review with `gh pr edit <n> --add-reviewer @copilot`, then work through the threads.
  - Act only on comments by Copilot or the owner.
  - Fix valid comments test-first, reply to the others with reasoning, and resolve every thread with `gh api graphql` (the MCP token can't resolve threads).
- [ ] **Step 4 (owner):** Read the PR, then squash-merge on GitHub. Claude never merges.
- [ ] **Step 5 (owner, optional):** Deploy with `just deploy-dev`, following runbook B2. Plan 2 changes no infrastructure. The Lambda package grows by the domain code, which nothing calls yet, and the smoke tests stay the same.

## Plan 2 is done when

- [ ] `just lint test tools-test detection-report` passes locally, and CI passes on the PR.
- [ ] Domain coverage is at least 85%, and every detector's precision and recall are at least 0.90 on the generated suite.
- [ ] PR merged through the review loop, every thread resolved.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §8.1 input, gzip by magic bytes, header or default v2, unknown fields, required and optional fields | Tasks 2, 3 |
| §8.1 validation, NODATA/SKIPDATA, limits, the 5% rejection rule, rejected samples | Tasks 2, 3 |
| §8.1 `NetworkFlow` output with the `source` block and `line_no` | Tasks 1, 2 |
| §8.2 internal addresses, windows on start times, fingerprints, evidence of 50 flows | Tasks 1, 4 |
| §8.2 `port_scan` v1, `remote_access_bruteforce` v1, `outbound_volume` v1: rules, severities, ATT&CK candidates | Tasks 5, 6, 7 |
| §8.2 quality measurement: seeded labeled datasets, precision and recall per detector, the CI report, the 0.9 target | Tasks 9, 10 |
| §5 finding and evidence shapes; at most 50 findings per upload, highest severity first, the rest counted | Tasks 4, 8 |
| §5 `detectors` reference data (id, name, description, version, candidate techniques) | Task 8 (`DETECTORS`); the table sync is Plan 3 |
| §11.4 Hypothesis property tests on the parser; the zip-bomb fixture; 85% domain coverage | Tasks 3, 8 |
| §8.6 "not a flow log" and "limit exceeded" failures | Task 3 (error codes); marking the upload `failed` is Plan 4 |
| §8.3–8.5 AI triage, §9 metrics, persistence and the `analyze` worker | Not in Plan 2 (Plans 3–5) |
