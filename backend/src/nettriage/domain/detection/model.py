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
