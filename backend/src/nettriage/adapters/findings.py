"""Reading findings (spec §5.2, §7), as `app_api` in the org's transaction: lists a page at a
time, and one finding with its evidence, techniques and history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, Engine, Row, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound

type FindingStatus = Literal["open", "investigating", "resolved", "false_positive"]
type FindingSeverity = Literal["low", "medium", "high", "critical"]
type FindingSort = Literal["newest", "severity"]

# A finding's detail lists at most this many of its latest events.
MAX_EVENTS = 100

_SUMMARY = (
    "f.id, f.upload_id, f.detector_id, f.detector_version, f.severity, f.status, f.title, "
    "host(f.src_ip) AS src_ip, host(f.dst_ip) AS dst_ip, f.dst_port, f.protocol, "
    "lower(f.time_window) AS window_start, upper(f.time_window) AS window_end, "
    "f.assignee_id, f.version, f.created_at"
)

# Severity as a number to sort by, most severe highest (Plan 6b).
_RANK = "array_position(ARRAY['low', 'medium', 'high', 'critical'], {})"
_ORDERS: dict[FindingSort, tuple[str, str]] = {
    # (the rows after the cursor, the sort order)
    "newest": (
        "(f.created_at, f.id) < (CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))",
        "f.created_at DESC, f.id DESC",
    ),
    "severity": (
        f"({_RANK.format('f.severity')}, f.created_at, f.id) < "
        f"({_RANK.format('CAST(:before_severity AS text)')}, "
        "CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))",
        f"{_RANK.format('f.severity')} DESC, f.created_at DESC, f.id DESC",
    ),
}


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
class AiAnalysis:
    """The finding's latest AI analysis (spec §7): what the model said, when it succeeded, or
    why there's no explanation (`error_code`), and what it cost."""

    id: UUID
    status: str
    provider: str
    model_id: str
    prompt_version: str
    output_schema_version: str
    output: dict[str, Any] | None
    error_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int | None
    # A reader's rating of a succeeded explanation (`up` or `down`), and who gave it.
    feedback: str | None
    feedback_by: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FindingDetail:
    finding: FindingSummary
    metrics: dict[str, Any]
    evidence: list[Evidence]
    techniques: list[FindingTechnique]
    events: list[FindingEvent]
    events_total: int
    ai_analysis: AiAnalysis | None


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
    sort: FindingSort = "newest",
    before: tuple[datetime, UUID] | None = None,
    before_severity: FindingSeverity | None = None,
) -> list[FindingSummary]:
    """The org's findings, newest first or most severe first (then newest), narrowed by the
    filters that are set. A page continues after `before`, and after `before_severity` too when
    sorted by severity."""
    before_at, before_id = before or (None, None)
    after_cursor, order = _ORDERS[sort]
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                f"SELECT {_SUMMARY} FROM findings f WHERE f.org_id = :org "  # noqa: S608
                "AND (CAST(:status AS text) IS NULL OR f.status = :status) "
                "AND (CAST(:severity AS text) IS NULL OR f.severity = :severity) "
                "AND (CAST(:detector AS text) IS NULL OR f.detector_id = :detector) "
                "AND (CAST(:upload AS uuid) IS NULL OR f.upload_id = :upload) "
                f"AND (CAST(:before_at AS timestamptz) IS NULL OR {after_cursor}) "
                f"ORDER BY {order} LIMIT :limit"
            ),
            {
                "org": org_id,
                "status": filters.status,
                "severity": filters.severity,
                "detector": filters.detector,
                "upload": filters.upload_id,
                "before_at": before_at,
                "before_id": before_id,
                "before_severity": before_severity,
                "limit": limit,
            },
        ).all()
    return [_summary(row) for row in rows]


def get_finding(engine: Engine, org_id: UUID, user_id: UUID, finding_id: UUID) -> FindingDetail:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        return read_finding(connection, org_id, finding_id)


def read_finding(connection: Connection, org_id: UUID, finding_id: UUID) -> FindingDetail:
    """One finding in full, inside the caller's transaction (triage returns what it wrote)."""
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
        events_total=_event_count(connection, finding_id),
        ai_analysis=_latest_analysis(connection, finding_id),
    )


_ANALYSIS = (
    "id, status, provider, model_id, prompt_version, output_schema_version, output, error_code, "
    "input_tokens, output_tokens, cost_usd, latency_ms, feedback, feedback_by, created_at, "
    "updated_at"
)


def _latest_analysis(connection: Connection, finding_id: UUID) -> AiAnalysis | None:
    row = connection.execute(
        text(
            f"SELECT {_ANALYSIS} FROM ai_analyses WHERE finding_id = :id "  # noqa: S608
            "ORDER BY updated_at DESC, id DESC LIMIT 1"
        ),
        {"id": finding_id},
    ).one_or_none()
    return None if row is None else AiAnalysis(**row._mapping)


def read_analysis(connection: Connection, analysis_id: UUID) -> AiAnalysis:
    """One analysis of the connection's org, by its ID."""
    row = connection.execute(
        text(f"SELECT {_ANALYSIS} FROM ai_analyses WHERE id = :id"),  # noqa: S608
        {"id": analysis_id},
    ).one()
    return AiAnalysis(**row._mapping)


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
    """The latest `MAX_EVENTS`, oldest first: history only grows, and the detail must stay
    inside the API's 6 MB response limit (the owner's decision, Plan 4c)."""
    rows = connection.execute(
        text(
            "SELECT * FROM (SELECT id, type, actor_id, payload, created_at FROM finding_events "
            "WHERE finding_id = :id ORDER BY created_at DESC, id DESC LIMIT :limit) latest "
            "ORDER BY created_at, id"
        ),
        {"id": finding_id, "limit": MAX_EVENTS},
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


def _event_count(connection: Connection, finding_id: UUID) -> int:
    count: int = connection.execute(
        text("SELECT count(*) FROM finding_events WHERE finding_id = :id"), {"id": finding_id}
    ).scalar_one()
    return count


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
