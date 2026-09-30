"""A finding's assignee can always triage it (the owner's decisions, Plan 4c). When a member
leaves, is removed or becomes a viewer, their findings in that org are unassigned, inside the
same transaction as the membership change, and each finding's history records who did it and
why. The foreign key's `SET NULL` (migration 0007) is only the backstop."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID, uuid7

from sqlalchemy import Connection, text

type Reason = Literal["member_left", "member_removed", "role_changed"]


def release_assignments(
    connection: Connection, org_id: UUID, member_id: UUID, *, actor_id: UUID, reason: Reason
) -> int:
    """Unassign the member's findings in the org. Returns how many there were."""
    released: list[UUID] = list(
        connection.execute(
            text(
                "UPDATE findings SET assignee_id = NULL, version = version + 1 "
                "WHERE org_id = :org AND assignee_id = :member RETURNING id"
            ),
            {"org": org_id, "member": member_id},
        ).scalars()
    )
    if released:
        payload = json.dumps({"from": str(member_id), "to": None, "reason": reason})
        connection.execute(
            text(
                "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type, payload) "
                "VALUES (:id, :org, :finding, :actor, 'assigned', CAST(:payload AS jsonb))"
            ),
            [
                {
                    "id": uuid7(),
                    "org": org_id,
                    "finding": finding_id,
                    "actor": actor_id,
                    "payload": payload,
                }
                for finding_id in released
            ],
        )
    return len(released)
