"""The append-only audit log in Postgres (spec §5.2, §9.4)."""

from __future__ import annotations

import json
from uuid import uuid7

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
