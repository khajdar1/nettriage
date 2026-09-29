"""The append-only audit log in Postgres (spec §5.2, §9.4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.audit import AuditEvent

MAX_USER_AGENT = 256

_INSERT = text(
    "INSERT INTO audit_log (id, org_id, actor_user_id, actor_type, action, target_type, "
    "target_id, outcome, ip, user_agent, request_id, trace_id, details) VALUES (:id, :org_id, "
    ":actor_user_id, :actor_type, :action, :target_type, :target_id, :outcome, "
    "CAST(:ip AS inet), :user_agent, :request_id, :trace_id, CAST(:details AS jsonb))"
)


def record(engine: Engine, event: AuditEvent) -> None:
    """Append one event, in its own transaction scoped to the event's org and actor."""
    with tenant_transaction(engine, org_id=event.org_id, user_id=event.actor_user_id) as connection:
        connection.execute(
            _INSERT,
            {
                "id": uuid7(),
                "org_id": event.org_id,
                "actor_user_id": event.actor_user_id,
                "actor_type": event.actor_type,
                "action": event.action,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "outcome": event.outcome,
                "ip": event.ip,
                "user_agent": event.user_agent[:MAX_USER_AGENT] if event.user_agent else None,
                "request_id": event.request_id,
                "trace_id": event.trace_id,
                "details": json.dumps(dict(event.details)),
            },
        )


@dataclass(frozen=True)
class AuditEntry:
    id: UUID
    created_at: datetime
    actor_user_id: UUID | None
    actor_type: str
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    details: dict[str, object]


def list_org_events(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[AuditEntry]:
    """The org's events, newest first; `before` continues after the last entry of a page.
    IPs and user agents stay out: they're for investigations, not the org's view."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                "SELECT id, created_at, actor_user_id, actor_type, action, target_type, "
                "target_id, outcome, details FROM audit_log WHERE org_id = :org "
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (created_at, id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            {"org": org_id, "before_at": before_at, "before_id": before_id, "limit": limit},
        ).all()
    return [
        AuditEntry(
            id=row.id,
            created_at=row.created_at,
            actor_user_id=row.actor_user_id,
            actor_type=row.actor_type,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            outcome=row.outcome,
            details=row.details,
        )
        for row in rows
    ]
