"""Row-level security and the API role's grants (spec §5.3, §5.4). Every query here runs as
`app_api`, the role the API Lambda uses."""

from uuid import UUID, uuid7

import pytest
from conftest import Database
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from tenantdata import add_invitation, add_member, add_tenant

from nettriage.adapters.postgres import tenant_transaction

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
    """The spec's dedicated isolation test: no WHERE clause, and still no other tenant's rows."""
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with tenant_transaction(database.app_api, org_id=mine.org_id) as connection:
        seen = {
            table: org_ids(connection, table)
            for table in ("organizations", "memberships", "invitations")
        }

    assert seen == {table: {mine.org_id} for table in seen}
    assert all(theirs.org_id not in ids for ids in seen.values())


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
