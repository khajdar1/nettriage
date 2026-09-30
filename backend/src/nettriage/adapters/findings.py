"""Reading findings (spec §5.2, §7), as `app_api` in the org's transaction: lists a page at a
time, and one finding with its evidence, techniques and history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, Row, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound

type FindingStatus = Literal["open", "investigating", "resolved", "false_positive"]
type FindingSeverity = Literal["low", "medium", "high", "critical"]

_SUMMARY = (
    "f.id, f.upload_id, f.detector_id, f.detector_version, f.severity, f.status, f.title, "
    "host(f.src_ip) AS src_ip, host(f.dst_ip) AS dst_ip, f.dst_port, f.protocol, "
    "lower(f.time_window) AS window_start, upper(f.time_window) AS window_end, "
    "f.assignee_id, f.version, f.created_at"
)


@dataclass(frozen=True)
class FindingSummary:
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


@dataclass(frozen=True)
class Evidence:
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


@dataclass(frozen=True)
class FindingTechnique:
    id: str
    name: str
    url: str
    source: str
    rationale: str | None


@dataclass(frozen=True)
class FindingEvent:
    id: UUID
    type: str
    actor_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class FindingDetail:
    finding: FindingSummary
    metrics: dict[str, Any]
    evidence: list[Evidence]
    techniques: list[FindingTechnique]
    events: list[FindingEvent]


@dataclass(frozen=True)
class FindingFilters:
    status: FindingStatus | None = None
    severity: FindingSeverity | None = None
    detector: str | None = None
    upload_id: UUID | None = None


def list_findings(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    filters: FindingFilters,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[FindingSummary]:
    """The org's findings, newest first, narrowed by the filters that are set."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                f"SELECT {_SUMMARY} FROM findings f WHERE f.org_id = :org "  # noqa: S608
                "AND (CAST(:status AS text) IS NULL OR f.status = :status) "
                "AND (CAST(:severity AS text) IS NULL OR f.severity = :severity) "
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (f.created_at, f.id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY f.created_at DESC, f.id DESC LIMIT :limit"
            ),
            {
                "org": org_id,
                "status": filters.status,
                "severity": filters.severity,
                "detector": filters.detector,
                "upload": filters.upload_id,
                "before_at": before_at,
                "before_id": before_id,
                "limit": limit,
            },
        ).all()
    return [_summary(row) for row in rows]


def get_finding(engine: Engine, org_id: UUID, user_id: UUID, finding_id: UUID) -> FindingDetail:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        row = connection.execute(
            text(
                f"SELECT {_SUMMARY}, f.metrics FROM findings f "  # noqa: S608
                "WHERE f.org_id = :org AND f.id = :id"
            ),
            {"org": org_id, "id": finding_id},
        ).one_or_none()
        if row is None:
            raise NotFound("No such finding.")
        return FindingDetail(
            finding=_summary(row),
            metrics=row.metrics,
            evidence=_evidence(connection, finding_id),
            techniques=_techniques(connection, finding_id),
            events=_events(connection, finding_id),
        )


def _evidence(connection: Connection, finding_id: UUID) -> list[Evidence]:
    rows = connection.execute(
        text(
            "SELECT host(src_ip) AS src_ip, host(dst_ip) AS dst_ip, src_port, dst_port, "
            "protocol, packets, bytes, start_ts, end_ts, action, line_no FROM finding_evidence "
            "WHERE finding_id = :id ORDER BY start_ts, line_no"
        ),
        {"id": finding_id},
    ).all()
    return [
        Evidence(
            src_ip=row.src_ip,
            dst_ip=row.dst_ip,
            src_port=row.src_port,
            dst_port=row.dst_port,
            protocol=row.protocol,
            packets=row.packets,
            bytes=row.bytes,
            start=row.start_ts,
            end=row.end_ts,
            action=row.action,
            line_no=row.line_no,
        )
        for row in rows
    ]


def _techniques(connection: Connection, finding_id: UUID) -> list[FindingTechnique]:
    rows = connection.execute(
        text(
            "SELECT t.id, t.name, t.url, ft.source, ft.rationale FROM finding_techniques ft "
            "JOIN attack_techniques t ON t.id = ft.technique_id "
            "WHERE ft.finding_id = :id ORDER BY ft.source, t.id"
        ),
        {"id": finding_id},
    ).all()
    return [
        FindingTechnique(
            id=row.id, name=row.name, url=row.url, source=row.source, rationale=row.rationale
        )
        for row in rows
    ]


def _events(connection: Connection, finding_id: UUID) -> list[FindingEvent]:
    rows = connection.execute(
        text(
            "SELECT id, type, actor_id, payload, created_at FROM finding_events "
            "WHERE finding_id = :id ORDER BY created_at, id"
        ),
        {"id": finding_id},
    ).all()
    return [
        FindingEvent(
            id=row.id,
            type=row.type,
            actor_id=row.actor_id,
            payload=row.payload,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _summary(row: Row[Any]) -> FindingSummary:
    return FindingSummary(
        id=row.id,
        upload_id=row.upload_id,
        detector_id=row.detector_id,
        detector_version=row.detector_version,
        severity=row.severity,
        status=row.status,
        title=row.title,
        src_ip=row.src_ip,
        dst_ip=row.dst_ip,
        dst_port=row.dst_port,
        protocol=row.protocol,
        window_start=row.window_start,
        window_end=row.window_end,
        assignee_id=row.assignee_id,
        version=row.version,
        created_at=row.created_at,
    )
