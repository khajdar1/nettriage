"""What the model sees (spec §8.3, §8.4): one finding's typed fields as JSON, never free text from
the uploaded file, and its hash, which keys the cache."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

PROMPT_VERSION = "v1"
# A candidate technique's description is cut to this many characters (spec §8.3).
MAX_DESCRIPTION = 500


@dataclass(frozen=True)
class EvidenceRow:
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: int
    packets: int
    bytes: int
    start: datetime
    end: datetime
    action: str


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class TriageSubject:
    """A finding as the triage worker reads it: only typed, validated fields."""

    org_id: UUID
    finding_id: UUID
    detector_id: str
    detector_version: int
    detector_name: str
    severity: str
    metrics: dict[str, Any]
    src_ip: str
    dst_ip: str | None
    dst_port: int | None
    protocol: int | None
    window_start: datetime
    window_end: datetime
    evidence: tuple[EvidenceRow, ...]
    candidates: tuple[Candidate, ...]


def user_content(subject: TriageSubject) -> dict[str, Any]:
    return {
        "detector": {
            "id": subject.detector_id,
            "version": subject.detector_version,
            "name": subject.detector_name,
        },
        "severity": subject.severity,
        "metrics": subject.metrics,
        "entities": {
            "src_ip": subject.src_ip,
            "dst_ip": subject.dst_ip,
            "dst_port": subject.dst_port,
            "protocol": subject.protocol,
        },
        "window": {"start": _iso(subject.window_start), "end": _iso(subject.window_end)},
        "evidence": [
            {
                "src_ip": row.src_ip,
                "dst_ip": row.dst_ip,
                "src_port": row.src_port,
                "dst_port": row.dst_port,
                "protocol": row.protocol,
                "packets": row.packets,
                "bytes": row.bytes,
                "start": _iso(row.start),
                "end": _iso(row.end),
                "action": row.action,
            }
            for row in subject.evidence
        ],
        "candidate_techniques": [
            {"id": c.id, "name": c.name, "description": excerpt(c.description)}
            for c in subject.candidates
        ],
    }


def canonical_json(content: dict[str, Any]) -> str:
    """The same content always gives the same text: sorted keys, no spaces."""
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(content).encode()).hexdigest()


def excerpt(description: str) -> str:
    if len(description) <= MAX_DESCRIPTION:
        return description
    cut = description[: MAX_DESCRIPTION - 1]
    return cut[: cut.rfind(" ")].rstrip() + "…" if " " in cut else cut + "…"


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")
