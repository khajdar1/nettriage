"""Triage in Postgres (spec §5.2, §5.4): the API may change a finding's status and assignee and
add to its history, nothing more, and an assignee is always a member of the finding's org."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from tenantdata import Tenant, add_member, add_tenant, add_user

from nettriage.adapters.postgres import tenant_transaction


def run_as_api(database: Database, tenant: Tenant, statement: str, **params: object) -> int:
    with tenant_transaction(database.app_api, org_id=tenant.org_id) as connection:
        return connection.execute(text(statement), params).rowcount


def finding(database: Database, finding_id: UUID) -> tuple[str, UUID | None, int]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text("SELECT status, assignee_id, version FROM findings WHERE id = :id"),
            {"id": finding_id},
        ).one()
    return row.status, row.assignee_id, row.version


def test_the_api_can_change_a_findings_status_assignee_and_version(database: Database) -> None:
    tenant = add_tenant(database.admin)

    changed = run_as_api(
        database,
        tenant,
        "UPDATE findings SET status = 'investigating', assignee_id = :owner, "
        "version = version + 1 WHERE id = :id",
        owner=tenant.owner_id,
        id=tenant.finding_id,
    )

    assert changed == 1
    assert finding(database, tenant.finding_id) == ("investigating", tenant.owner_id, 2)


def test_the_api_records_history_as_itself(database: Database) -> None:
    tenant = add_tenant(database.admin)

    added = run_as_api(
        database,
        tenant,
        "INSERT INTO finding_events (id, org_id, finding_id, actor_id, type, payload) "
        "VALUES (:id, :org, :finding, :actor, 'commented', '{\"text\": \"Looks benign\"}')",
        id=uuid7(),
        org=tenant.org_id,
        finding=tenant.finding_id,
        actor=tenant.owner_id,
    )

    assert added == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE findings SET title = 'Nothing to see'",
        "UPDATE findings SET severity = 'low'",
        "UPDATE findings SET org_id = gen_random_uuid()",
        "DELETE FROM findings",
        "UPDATE finding_events SET payload = '{}'",
        "DELETE FROM finding_events",
        "INSERT INTO finding_events (id, org_id, finding_id, type, created_at) "
        "SELECT gen_random_uuid(), org_id, id, 'commented', now() - interval '1 day' "
        "FROM findings",
        "DELETE FROM finding_evidence",
        "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
        "SELECT id, 'T1595', 'ai', org_id FROM findings",
    ],
)
def test_the_api_can_not_rewrite_a_finding_or_its_history(
    database: Database, statement: str
) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="permission denied"):
        run_as_api(database, tenant, statement)


def test_an_update_without_an_org_filter_changes_only_its_own_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    changed = run_as_api(database, mine, "UPDATE findings SET status = 'resolved'")

    assert changed == 1
    assert finding(database, theirs.finding_id)[0] == "open"


def test_an_assignee_must_be_a_member_of_the_findings_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(IntegrityError, match="findings_assignee_is_a_member"):
        run_as_api(
            database,
            mine,
            "UPDATE findings SET assignee_id = :outsider WHERE id = :id",
            outsider=theirs.owner_id,
            id=mine.finding_id,
        )


def test_a_membership_can_always_end_and_its_findings_are_unassigned(database: Database) -> None:
    """The API unassigns a leaver's findings itself; the foreign key is the backstop."""
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        analyst = add_user(connection)
        add_member(connection, tenant.org_id, analyst, "analyst")
        connection.execute(
            text("UPDATE findings SET assignee_id = :user WHERE id = :id"),
            {"user": analyst, "id": tenant.finding_id},
        )
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :user"),
            {"org": tenant.org_id, "user": analyst},
        )

    assert finding(database, tenant.finding_id) == ("open", None, 1)
