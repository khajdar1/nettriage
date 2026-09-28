"""Constraints the database enforces whatever the application does (spec §5.2)."""

from datetime import datetime
from uuid import uuid7

import pytest
from conftest import Database
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from tenantdata import add_invitation, add_tenant, add_user


def execute(engine: Engine, statement: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement), params)


def test_a_membership_role_must_be_one_of_the_four_roles(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with database.admin.begin() as connection:
        user = add_user(connection)

    with pytest.raises(IntegrityError):
        execute(
            database.admin,
            "INSERT INTO memberships (org_id, user_id, role) VALUES (:org, :user, 'superadmin')",
            org=tenant.org_id,
            user=user,
        )


@pytest.mark.parametrize(
    "slug", ["Upper", "two--dashes", "-leading", "trailing-", "sp ace", "x" * 61]
)
def test_organization_slugs_are_lowercase_words_joined_by_dashes(
    database: Database, slug: str
) -> None:
    with pytest.raises(IntegrityError):
        execute(
            database.admin,
            "INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', :slug)",
            id=uuid7(),
            slug=slug,
        )


def test_an_email_has_at_most_one_pending_invitation_per_org(database: Database) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(IntegrityError), database.admin.begin() as connection:
        add_invitation(
            connection, tenant.org_id, tenant.owner_id, f"INVITEE-{tenant.org_id.hex}@Example.com"
        )


def test_a_revoked_invitation_does_not_block_a_new_one(database: Database) -> None:
    tenant = add_tenant(database.admin)
    execute(
        database.admin,
        "UPDATE invitations SET revoked_at = now() WHERE id = :id",
        id=tenant.invitation_id,
    )

    with database.admin.begin() as connection:
        add_invitation(
            connection, tenant.org_id, tenant.owner_id, f"invitee-{tenant.org_id.hex}@example.com"
        )


def test_deleting_an_org_removes_its_memberships_and_invitations_not_its_audit_log(
    database: Database,
) -> None:
    tenant = add_tenant(database.admin)
    execute(
        database.admin,
        "INSERT INTO audit_log (id, org_id, actor_type, action, outcome) "
        "VALUES (:id, :org, 'system', 'test.event', 'success')",
        id=uuid7(),
        org=tenant.org_id,
    )

    execute(database.admin, "DELETE FROM organizations WHERE id = :id", id=tenant.org_id)

    with database.admin.begin() as connection:
        remaining: dict[str, int] = {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE org_id = :org"),  # noqa: S608
                {"org": tenant.org_id},
            ).scalar_one()
            for table in ("memberships", "invitations", "audit_log")
        }
    assert remaining == {"memberships": 0, "invitations": 0, "audit_log": 1}


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"]
)
def test_the_audit_log_is_append_only_even_for_a_superuser(
    database: Database, statement: str
) -> None:
    execute(
        database.admin,
        "INSERT INTO audit_log (id, actor_type, action, outcome) "
        "VALUES (:id, 'system', 'test.event', 'success')",
        id=uuid7(),
    )

    with pytest.raises(DBAPIError, match="append-only"):
        execute(database.admin, statement)


def test_updates_refresh_updated_at(database: Database) -> None:
    tenant = add_tenant(database.admin)
    query = text("SELECT updated_at FROM organizations WHERE id = :id")
    with database.admin.begin() as connection:
        before: datetime = connection.execute(query, {"id": tenant.org_id}).scalar_one()

    execute(
        database.admin,
        "UPDATE organizations SET name = 'Renamed' WHERE id = :id",
        id=tenant.org_id,
    )

    with database.admin.begin() as connection:
        after: datetime = connection.execute(query, {"id": tenant.org_id}).scalar_one()
    assert after > before
