"""Row-level security and the API role's grants (spec §5.3, §5.4). Every query here runs as
`app_api`, the role the API Lambda uses."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from tenantdata import add_invitation, add_member, add_tenant

from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.users import sign_in_user

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log")
INSERT_AUDIT_EVENT = text(
    "INSERT INTO audit_log (id, org_id, actor_type, action, outcome) "
    "VALUES (:id, :org, 'user', 'test.event', 'success')"
)


def org_ids(connection: Connection, table: str) -> set[UUID]:
    """The org IDs of every row the connection can see, with no WHERE clause."""
    column = "id" if table == "organizations" else "org_id"
    return set(connection.execute(text(f"SELECT {column} FROM {table}")).scalars())  # noqa: S608


def run_as_api(database: Database, statement: str, org_id: UUID, **params: object) -> None:
    with tenant_transaction(database.app_api, org_id=org_id) as connection:
        connection.execute(text(statement), params)


def test_a_query_without_an_org_filter_sees_only_its_own_org(database: Database) -> None:
    """The spec's dedicated isolation test: no WHERE clause, and still no other tenant's rows,
    with both settings set as the API sets them, even for a user who belongs to both orgs."""
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)
    with database.admin.begin() as connection:
        add_member(connection, theirs.org_id, mine.owner_id, "viewer")

    with tenant_transaction(
        database.app_api, org_id=mine.org_id, user_id=mine.owner_id
    ) as connection:
        seen = {
            table: org_ids(connection, table)
            for table in ("organizations", "memberships", "invitations")
        }

    assert seen == {table: {mine.org_id} for table in seen}


def two_orgs_one_user(database: Database) -> tuple[UUID, UUID, UUID]:
    """(A, B, U): U owns A and is a viewer in B."""
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)
    with database.admin.begin() as connection:
        add_member(connection, theirs.org_id, mine.owner_id, "viewer")
    return mine.org_id, theirs.org_id, mine.owner_id


def remaining(database: Database, statement: str, **params: object) -> int:
    with database.admin.begin() as connection:
        count: int = connection.execute(text(statement), params).scalar_one()
    return count


def test_another_tenants_org_cannot_be_deleted(database: Database) -> None:
    a, b, user = two_orgs_one_user(database)

    with tenant_transaction(database.app_api, org_id=a, user_id=user) as connection:
        deleted = connection.execute(
            text("DELETE FROM organizations WHERE id = :b"), {"b": b}
        ).rowcount

    assert deleted == 0
    assert remaining(database, "SELECT count(*) FROM organizations WHERE id = :b", b=b) == 1


def test_leaving_without_an_org_filter_only_leaves_this_org(database: Database) -> None:
    a, b, user = two_orgs_one_user(database)

    with tenant_transaction(database.app_api, org_id=a, user_id=user) as connection:
        deleted = connection.execute(
            text("DELETE FROM memberships WHERE user_id = :user"), {"user": user}
        ).rowcount

    assert deleted == 1
    assert (
        remaining(
            database,
            "SELECT count(*) FROM memberships WHERE org_id = :b AND user_id = :user",
            b=b,
            user=user,
        )
        == 1
    )


def test_another_tenants_org_and_roles_cannot_be_changed(database: Database) -> None:
    a, b, user = two_orgs_one_user(database)

    with tenant_transaction(database.app_api, org_id=a, user_id=user) as connection:
        renamed = connection.execute(
            text("UPDATE organizations SET name = 'Taken over' WHERE id = :b"), {"b": b}
        ).rowcount
        promoted = connection.execute(
            text("UPDATE memberships SET role = 'owner' WHERE org_id = :b"), {"b": b}
        ).rowcount

    assert (renamed, promoted) == (0, 0)


def test_the_api_role_can_run_an_orgs_whole_lifecycle(database: Database) -> None:
    """Every write Plan 3b's endpoints need, as app_api, inside the org's own transaction."""
    org = uuid7()
    owner, member = (
        sign_in_user(database.app_api, sub=f"sub-{uuid7()}", email=f"{name}@example.com").user_id
        for name in ("owner", "member")
    )

    with tenant_transaction(database.app_api, org_id=org, user_id=owner) as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, created_by) "
                "VALUES (:id, 'New org', :slug, :owner)"
            ),
            {"id": org, "slug": f"org-{org.hex}", "owner": owner},
        )
        connection.execute(
            text(
                "INSERT INTO memberships (org_id, user_id, role) VALUES "
                "(:org, :owner, 'owner'), (:org, :member, 'viewer')"
            ),
            {"org": org, "owner": owner, "member": member},
        )
        add_invitation(connection, org, owner, "invitee@example.com")
        connection.execute(
            text(
                "UPDATE memberships SET role = 'analyst' WHERE org_id = :org AND user_id = :member"
            ),
            {"org": org, "member": member},
        )
        connection.execute(
            text("UPDATE organizations SET name = 'Renamed' WHERE id = :org"), {"org": org}
        )
        connection.execute(
            text("DELETE FROM memberships WHERE org_id = :org AND user_id = :member"),
            {"org": org, "member": member},
        )

    with tenant_transaction(database.app_api, user_id=owner) as connection:  # "my orgs"
        assert org_ids(connection, "organizations") == {org}

    with tenant_transaction(database.app_api, org_id=org, user_id=owner) as connection:
        deleted = connection.execute(
            text("DELETE FROM organizations WHERE id = :org"), {"org": org}
        ).rowcount
    assert deleted == 1


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_no_tenant_settings_means_no_rows(database: Database, table: str) -> None:
    add_tenant(database.admin)

    with tenant_transaction(database.app_api) as connection:
        assert org_ids(connection, table) == set()


def test_a_pooled_connection_forgets_the_previous_tenant(database: Database) -> None:
    tenant = add_tenant(database.admin)
    with tenant_transaction(database.app_api, org_id=tenant.org_id) as connection:
        assert org_ids(connection, "invitations") == {tenant.org_id}

    with database.app_api.begin() as connection:  # the same pooled connection, no settings
        assert org_ids(connection, "invitations") == set()


def test_rows_for_another_org_cannot_be_written(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    def invite_into_their_org() -> None:
        with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
            add_invitation(connection, theirs.org_id, mine.owner_id, "someone@example.com")

    with pytest.raises(ProgrammingError, match="row-level security"):
        invite_into_their_org()


def test_a_user_sees_their_memberships_and_orgs_everywhere_but_not_other_invitations(
    database: Database,
) -> None:
    first, second = add_tenant(database.admin), add_tenant(database.admin)
    with database.admin.begin() as connection:
        add_member(connection, second.org_id, first.owner_id, "viewer")

    with tenant_transaction(database.app_api, user_id=first.owner_id) as connection:
        orgs = org_ids(connection, "organizations")
        memberships = org_ids(connection, "memberships")
        invitations = org_ids(connection, "invitations")

    assert orgs == memberships == {first.org_id, second.org_id}
    assert invitations == set()


def test_audit_events_may_have_no_org_and_are_read_per_org(database: Database) -> None:
    mine = add_tenant(database.admin)

    with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
        connection.execute(INSERT_AUDIT_EVENT, {"id": uuid7(), "org": None})
        connection.execute(INSERT_AUDIT_EVENT, {"id": uuid7(), "org": mine.org_id})
        assert org_ids(connection, "audit_log") == {mine.org_id}


def test_audit_events_cannot_be_written_for_another_org(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    def log_into_their_org() -> None:
        with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
            connection.execute(INSERT_AUDIT_EVENT, {"id": uuid7(), "org": theirs.org_id})

    with pytest.raises(ProgrammingError, match="row-level security"):
        log_into_their_org()


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
        "UPDATE organizations SET slug = 'taken-over'",
        "UPDATE users SET cognito_sub = 'someone-else'",
        "UPDATE invitations SET token_hash = repeat('0', 64)",
        "INSERT INTO organizations (id, name, slug, is_demo) "
        "VALUES (gen_random_uuid(), 'Demo', 'fake-demo', true)",
        "CREATE TEMP TABLE shadow (id int)",
        "CREATE TABLE sneaky (id int)",
    ],
)
def test_the_api_role_only_has_the_grants_it_needs(database: Database, statement: str) -> None:
    tenant = add_tenant(database.admin)

    with pytest.raises(ProgrammingError, match="permission denied"):
        run_as_api(database, statement, tenant.org_id)


def test_the_api_role_cannot_bypass_row_level_security(database: Database) -> None:
    with database.app_api.connect() as connection:
        flags = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        owned: int = connection.execute(
            text("SELECT count(*) FROM pg_tables WHERE tableowner = current_user")
        ).scalar_one()

    assert tuple(flags) == (False, False)
    assert owned == 0


def test_an_invalid_tenant_setting_is_an_error_not_a_match(database: Database) -> None:
    def query_with_a_bad_setting() -> None:
        with database.app_api.begin() as connection:
            connection.execute(text("SELECT set_config('app.org_id', 'not-a-uuid', true)"))
            connection.execute(text("SELECT count(*) FROM invitations"))

    with pytest.raises(DBAPIError, match="uuid"):
        query_with_a_bad_setting()
