"""Response bodies for findings and ATT&CK techniques (spec §7). Findings are read-only here;
Plan 4c adds triage."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from nettriage.adapters.attack_techniques import Technique
from nettriage.adapters.findings import (
    Evidence,
    FindingDetail,
    FindingEvent,
    FindingSeverity,
    FindingStatus,
    FindingSummary,
    FindingTechnique,
)


class FindingSummaryOut(BaseModel):
    id: UUID
    upload_id: UUID
    detector_id: str
    detector_version: int
    severity: FindingSeverity
    status: FindingStatus
    title: str
    src_ip: str
    dst_ip: str | None
    dst_port: int | None
    protocol: int | None
    window_start: datetime
    window_end: datetime
    assignee_id: UUID | None
    version: int
    created_at: datetime

    @classmethod
    def of(cls, finding: FindingSummary) -> FindingSummaryOut:
        return cls(**vars(finding))


class FindingsOut(BaseModel):
    findings: list[FindingSummaryOut]
    next_cursor: str | None


class EvidenceOut(BaseModel):
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
    line_no: int

    @classmethod
    def of(cls, evidence: Evidence) -> EvidenceOut:
        return cls(**vars(evidence))


class FindingTechniqueOut(BaseModel):
    id: str
    name: str
    url: str
    source: str
    rationale: str | None

    @classmethod
    def of(cls, technique: FindingTechnique) -> FindingTechniqueOut:
        return cls(**vars(technique))


class FindingEventOut(BaseModel):
    id: UUID
    type: str
    actor_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, event: FindingEvent) -> FindingEventOut:
        return cls(**vars(event))


class FindingOut(FindingSummaryOut):
    metrics: dict[str, Any]
    evidence: list[EvidenceOut]
    techniques: list[FindingTechniqueOut]
    events: list[FindingEventOut]
    # How many events the finding has; `events` holds the latest 100 of them.
    events_total: int

    @classmethod
    def of_detail(cls, detail: FindingDetail) -> FindingOut:
        return cls(
            **vars(detail.finding),
            metrics=detail.metrics,
            evidence=[EvidenceOut.of(evidence) for evidence in detail.evidence],
            techniques=[FindingTechniqueOut.of(technique) for technique in detail.techniques],
            events=[FindingEventOut.of(event) for event in detail.events],
            events_total=detail.events_total,
        )


class TechniqueOut(BaseModel):
    id: str
    name: str
    tactics: list[str]
    description: str
    url: str
    is_subtechnique: bool
    parent_id: str | None
    deprecated: bool
    attack_version: str
    # ATT&CK's terms of use: MITRE's notice and license travel with every copy (spec §11.9).
    notice: str
    license: str

    @classmethod
    def of(cls, technique: Technique, notice: str, license: str) -> TechniqueOut:
        return cls(
            id=technique.id,
            name=technique.name,
            tactics=list(technique.tactics),
            description=technique.description,
            url=technique.url,
            is_subtechnique=technique.is_subtechnique,
            parent_id=technique.parent_id,
            deprecated=technique.deprecated,
            attack_version=technique.attack_version,
            notice=notice,
            license=license,
        )
