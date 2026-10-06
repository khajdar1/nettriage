"""An organization's overview (Plan 6d): its findings counted by severity and status, what's
unassigned and what's the caller's, what arrived in the last day, its members and its last upload.
One read in the org's transaction; every number is a count over existing tables."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.findings import FindingSeverity, FindingStatus
from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.uploads import Upload, latest_upload

SEVERITIES: tuple[FindingSeverity, ...] = ("critical", "high", "medium", "low")
STATUSES: tuple[FindingStatus, ...] = ("open", "investigating", "resolved", "false_positive")

# Open or Investigating: still to be dealt with.
_UNRESOLVED = "status IN ('open', 'investigating')"
_COUNTS = ", ".join(
    [
        *(
            f"count(*) FILTER (WHERE {_UNRESOLVED} AND severity = '{severity}') AS u_{severity}"
            for severity in SEVERITIES
        ),
        *(f"count(*) FILTER (WHERE status = '{status}') AS s_{status}" for status in STATUSES),
        f"count(*) FILTER (WHERE {_UNRESOLVED} AND assignee_id IS NULL) AS unassigned",
        f"count(*) FILTER (WHERE {_UNRESOLVED} AND assignee_id = :user) AS mine",
        "count(*) FILTER (WHERE created_at >= now() - interval '1 day') AS new_last_day",
    ]
)


@dataclass(frozen=True)
class Overview:
    unresolved_by_severity: dict[FindingSeverity, int]
    by_status: dict[FindingStatus, int]
    unresolved_unassigned: int
    unresolved_mine: int
    new_last_day: int
    member_count: int
    last_upload: Upload | None


def org_overview(engine: Engine, org_id: UUID, user_id: UUID) -> Overview:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        counts = connection.execute(
            text(f"SELECT {_COUNTS} FROM findings WHERE org_id = :org"),  # noqa: S608
            {"org": org_id, "user": user_id},
        ).one()
        members: int = connection.execute(
            text("SELECT count(*) FROM memberships WHERE org_id = :org"), {"org": org_id}
        ).scalar_one()
        last = latest_upload(connection, org_id)
    return Overview(
        unresolved_by_severity={
            severity: getattr(counts, f"u_{severity}") for severity in SEVERITIES
        },
        by_status={status: getattr(counts, f"s_{status}") for status in STATUSES},
        unresolved_unassigned=counts.unassigned,
        unresolved_mine=counts.mine,
        new_last_day=counts.new_last_day,
        member_count=members,
        last_upload=last,
    )
