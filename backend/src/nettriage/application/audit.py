"""Audit events (spec §9.4): who did what, to what, with what outcome."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Outcome = Literal["success", "denied", "error"]
ActorType = Literal["user", "system", "anonymous"]


@dataclass(frozen=True)
class AuditEvent:
    action: str
    outcome: Outcome
    actor_type: ActorType
    actor_user_id: UUID | None = None
    org_id: UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)
