"""A finding's assignee can always triage it (the owner's decisions, Plan 4c): a member who
leaves, is removed or becomes a viewer is unassigned from the org's findings, and each finding's
history records who did it and why."""

from uuid import UUID

from conftest import Database
from sqlalchemy import text
from tenantdata import Tenant, add_finding, add_member, add_tenant, add_user

from nettriage.adapters.findings import get_finding
from nettriage.adapters.organizations import change_role, remove_member
from nettriage.adapters.triage import Triage, triage_finding


def assigned_analyst(database: Database, tenant: Tenant) -> UUID:
    with database.admin.begin() as connection:
        analyst = add_user(connection)
        add_member(connection, tenant.org_id, analyst, "analyst")
    triage_finding(
        database.app_api,
        tenant.org_id,
        tenant.owner_id,
        tenant.finding_id,
        expected_version=1,
        change=Triage(assign=True, assignee_id=analyst),
    )
    return analyst


def state(database: Database, tenant: Tenant) -> tuple[UUID | None, int, dict[str, object], UUID]:
    """The finding's assignee and version, and its latest event's payload and actor."""
    detail = get_finding(database.app_api, tenant.org_id, tenant.owner_id, tenant.finding_id)
    last = detail.events[-1]
    assert last.actor_id is not None
    return detail.finding.assignee_id, detail.finding.version, last.payload, last.actor_id


def test_a_member_who_leaves_is_unassigned(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    remove_member(database.app_api, tenant.org_id, actor_id=analyst, target_id=analyst)

    assert state(database, tenant) == (
        None,
        3,
        {"from": str(analyst), "to": None, "reason": "member_left"},
        analyst,
    )


def test_a_removed_member_is_unassigned_by_whoever_removed_them(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    remove_member(database.app_api, tenant.org_id, actor_id=tenant.owner_id, target_id=analyst)

    assert state(database, tenant) == (
        None,
        3,
        {"from": str(analyst), "to": None, "reason": "member_removed"},
        tenant.owner_id,
    )


def test_a_member_who_becomes_a_viewer_is_unassigned(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    change_role(
        database.app_api, tenant.org_id, actor_id=tenant.owner_id, target_id=analyst, role="viewer"
    )

    assert state(database, tenant) == (
        None,
        3,
        {"from": str(analyst), "to": None, "reason": "role_changed"},
        tenant.owner_id,
    )


def test_a_role_that_can_still_triage_keeps_its_assignments(database: Database) -> None:
    tenant = add_tenant(database.admin)
    analyst = assigned_analyst(database, tenant)

    change_role(
        database.app_api, tenant.org_id, actor_id=tenant.owner_id, target_id=analyst, role="admin"
    )

    assert state(database, tenant)[:2] == (analyst, 2)


def test_leaving_one_org_keeps_assignments_in_another(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)
    analyst = assigned_analyst(database, mine)
    with database.admin.begin() as connection:
        add_member(connection, theirs.org_id, analyst, "analyst")
        other = add_finding(connection, theirs.org_id, theirs.upload_id)
        connection.execute(
            text("UPDATE findings SET assignee_id = :user WHERE id = :id"),
            {"user": analyst, "id": other},
        )

    remove_member(database.app_api, mine.org_id, actor_id=analyst, target_id=analyst)

    with database.admin.begin() as connection:
        kept: UUID = connection.execute(
            text("SELECT assignee_id FROM findings WHERE id = :id"), {"id": other}
        ).scalar_one()
    assert kept == analyst
