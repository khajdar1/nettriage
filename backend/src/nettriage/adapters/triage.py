"""Triaging a finding (spec §7), as `app_api` in the org's transaction: change its status or
assignee against the version the caller read, or comment on it. Every change is an event in the
finding's history, with the caller as its actor.

Like every change since Plan 3c, the caller's role is re-read under the org's lock, and the
finding's row is locked too, so two members triaging at once can't both win: the second gets
`StaleVersion`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text

from nettriage.adapters.findings import FindingDetail, FindingEvent, FindingStatus, read_finding
from nettriage.adapters.organizations import lock_org_for
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound, require
from nettriage.application.permissions import Role
from nettriage.application.triage import InvalidAssignee, StaleVersion, can_be_assigned


@dataclass(frozen=True)
class Triage:
    """What to change. `status` None leaves it as it is; `assign` sets the assignee to
    `assignee_id`, and `assignee_id` None then unassigns."""

    status: FindingStatus | None = None
    assign: bool = False
    assignee_id: UUID | None = None


@dataclass(frozen=True)
class Triaged:
    """The finding as it now is, and what changed: (from, to) pairs, or None."""

    detail: FindingDetail
    status: tuple[str, str] | None
    assignee: tuple[UUID | None, UUID | None] | None


def triage_finding(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    finding_id: UUID,
    *,
    expected_version: int,
    change: Triage,
) -> Triaged:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "findings:triage")
        row = connection.execute(
            text(
                "SELECT status, assignee_id, version FROM findings "
                "WHERE org_id = :org AND id = :id FOR UPDATE"
            ),
            {"org": org_id, "id": finding_id},
        ).one_or_none()
        if row is None:
            raise NotFound("No such finding.")
        if row.version != expected_version:
            raise StaleVersion(row.version)
        status = None
        if change.status is not None and change.status != row.status:
            status = (row.status, change.status)
        assignee = None
        if change.assign and change.assignee_id != row.assignee_id:
            if change.assignee_id is not None and not can_be_assigned(
                _role(connection, org_id, change.assignee_id)
            ):
                raise InvalidAssignee(
                    "Assign the finding to an owner, admin or analyst of this organization."
                )
            assignee = (row.assignee_id, change.assignee_id)
        if status is not None or assignee is not None:
            connection.execute(
                text(
                    "UPDATE findings SET status = :status, assignee_id = :assignee, "
                    "version = version + 1 WHERE org_id = :org AND id = :id"
                ),
                {
                    "status": status[1] if status else row.status,
                    "assignee": assignee[1] if assignee else row.assignee_id,
                    "org": org_id,
                    "id": finding_id,
                },
            )
        if status is not None:
            _record(connection, org_id, finding_id, user_id, "status_changed", _pair(status))
        if assignee is not None:
            _record(connection, org_id, finding_id, user_id, "assigned", _pair(assignee))
        return Triaged(read_finding(connection, org_id, finding_id), status, assignee)


def add_comment(
    engine: Engine, org_id: UUID, user_id: UUID, finding_id: UUID, comment: str
) -> FindingEvent:
    """A comment is an event in the finding's history. It doesn't change the finding's version,
    so it never makes someone else's status change stale."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "findings:comment")
        exists = connection.execute(
            text("SELECT 1 FROM findings WHERE org_id = :org AND id = :id"),
            {"org": org_id, "id": finding_id},
        ).scalar_one_or_none()
        if exists is None:
            raise NotFound("No such finding.")
        return _record(connection, org_id, finding_id, user_id, "commented", {"text": comment})


def _record(
    connection: Connection,
    org_id: UUID,
    finding_id: UUID,
    actor_id: UUID,
    event_type: str,
    payload: dict[str, Any],
) -> FindingEvent:
    row = connection.execute(
        text(
            "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type, payload) "
            "VALUES (:id, :org, :finding, :actor, :type, CAST(:payload AS jsonb)) "
            "RETURNING id, type, actor_id, payload, created_at"
        ),
        {
            "id": uuid7(),
            "org": org_id,
            "finding": finding_id,
            "actor": actor_id,
            "type": event_type,
            "payload": json.dumps(payload),
        },
    ).one()
    return FindingEvent(
        id=row.id,
        type=row.type,
        actor_id=row.actor_id,
        payload=row.payload,
        created_at=row.created_at,
    )


def _pair(change: tuple[Any, Any]) -> dict[str, Any]:
    before, after = change
    return {
        "from": str(before) if before is not None else None,
        "to": str(after) if after is not None else None,
    }


def _role(connection: Connection, org_id: UUID, user_id: UUID) -> Role | None:
    role: Role | None = connection.execute(
        text("SELECT role FROM memberships WHERE org_id = :org AND user_id = :user"),
        {"org": org_id, "user": user_id},
    ).scalar_one_or_none()
    return role
