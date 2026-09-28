"""Just-in-time sign-in and row-level security on `users` (spec §5.3, §6.3). Queries run as
`app_api`, the role the API uses; seeding uses the superuser engine."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from conftest import Database
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError
from tenantdata import add_member, add_org, add_user

from nettriage.adapters.postgres import tenant_transaction
from nettriage.adapters.users import load_me, sign_in_user


def visible_users(connection: Connection) -> set[UUID]:
    return set(connection.execute(text("SELECT id FROM users")).scalars())


def test_the_first_sign_in_creates_the_user_and_later_ones_find_them(database: Database) -> None:
    sub = f"sub-{uuid4()}"

    first = sign_in_user(database.app_api, sub=sub, email="new@example.com")
    again = sign_in_user(database.app_api, sub=sub, email="new@example.com")

    assert (first.created, again.created) == (True, False)
    assert first.user_id == again.user_id
    assert not first.disabled


def test_sign_in_keeps_the_email_current(database: Database) -> None:
    sub = f"sub-{uuid4()}"
    user = sign_in_user(database.app_api, sub=sub, email="old@example.com")

    sign_in_user(database.app_api, sub=sub, email="renamed@example.com")

    me = load_me(database.app_api, user.user_id)
    assert me is not None
    assert me.email == "renamed@example.com"


def test_a_disabled_account_is_reported_and_not_marked_as_signed_in(database: Database) -> None:
    sub = f"sub-{uuid4()}"
    user = sign_in_user(database.app_api, sub=sub, email="gone@example.com")
    with database.admin.begin() as connection:
        connection.execute(
            text("UPDATE users SET disabled_at = now(), last_login_at = NULL WHERE id = :id"),
            {"id": user.user_id},
        )

    result = sign_in_user(database.app_api, sub=sub, email="gone@example.com")

    assert result.disabled
    with database.admin.begin() as connection:
        last_login: datetime | None = connection.execute(
            text("SELECT last_login_at FROM users WHERE id = :id"), {"id": user.user_id}
        ).scalar_one()
    assert last_login is None


def test_me_lists_the_users_memberships_by_org_name(database: Database) -> None:
    with database.admin.begin() as connection:
        user = add_user(connection)
        first, second = add_org(connection, user), add_org(connection, user)
        add_member(connection, first, user, "owner")
        add_member(connection, second, user, "viewer")

    me = load_me(database.app_api, user)

    assert me is not None
    assert {(m.org_id, m.role) for m in me.memberships} == {
        (first, "owner"),
        (second, "viewer"),
    }


def test_me_is_none_for_an_unknown_user(database: Database) -> None:
    assert load_me(database.app_api, uuid4()) is None


def test_outside_an_org_a_user_sees_only_their_own_row(database: Database) -> None:
    with database.admin.begin() as connection:
        me, colleague = add_user(connection), add_user(connection)
        org = add_org(connection, me)
        add_member(connection, org, me, "owner")
        add_member(connection, org, colleague, "viewer")

    with tenant_transaction(database.app_api, user_id=me) as connection:
        assert visible_users(connection) == {me}


def test_inside_an_org_a_member_sees_that_orgs_members_only(database: Database) -> None:
    with database.admin.begin() as connection:
        me, colleague, stranger = add_user(connection), add_user(connection), add_user(connection)
        mine, theirs = add_org(connection, me), add_org(connection, stranger)
        add_member(connection, mine, me, "owner")
        add_member(connection, mine, colleague, "analyst")
        add_member(connection, theirs, stranger, "owner")

    with tenant_transaction(database.app_api, org_id=mine, user_id=me) as connection:
        assert visible_users(connection) == {me, colleague}


def test_a_non_member_sees_no_one_in_an_org(database: Database) -> None:
    with database.admin.begin() as connection:
        me, stranger = add_user(connection), add_user(connection)
        theirs = add_org(connection, stranger)
        add_member(connection, theirs, stranger, "owner")

    with tenant_transaction(database.app_api, org_id=theirs, user_id=me) as connection:
        assert visible_users(connection) == {me}


def test_without_settings_no_user_is_visible(database: Database) -> None:
    with database.admin.begin() as connection:
        add_user(connection)

    with tenant_transaction(database.app_api) as connection:
        assert visible_users(connection) == set()


def test_a_user_can_rename_only_themselves(database: Database) -> None:
    with database.admin.begin() as connection:
        me, colleague = add_user(connection), add_user(connection)
        org = add_org(connection, me)
        add_member(connection, org, me, "owner")
        add_member(connection, org, colleague, "viewer")

    with tenant_transaction(database.app_api, org_id=org, user_id=me) as connection:
        renamed = connection.execute(
            text("UPDATE users SET display_name = 'Taken over' WHERE id IN (:me, :other)"),
            {"me": me, "other": colleague},
        ).rowcount

    assert renamed == 1


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO users (id, cognito_sub, email) VALUES (gen_random_uuid(), 'x', 'x@y.z')",
        "UPDATE users SET email = 'x@y.z'",
        "UPDATE users SET disabled_at = NULL",
    ],
)
def test_the_api_role_changes_users_only_through_sign_in(
    database: Database, statement: str
) -> None:
    def run() -> None:
        with database.app_api.begin() as connection:
            connection.execute(text(statement))

    with pytest.raises(ProgrammingError, match="permission denied"):
        run()


def test_sign_in_works_when_the_owner_role_is_not_a_superuser(database: Database) -> None:
    """On Neon the migrations run as `neondb_owner`, which isn't a superuser. The function must
    still find users, which works because `users` doesn't FORCE row-level security."""
    sub = f"sub-{uuid4()}"
    existing = sign_in_user(database.app_api, sub=sub, email="owned@example.com")
    owner = f"owner_{uuid4().hex[:8]}"
    with database.admin.begin() as connection:
        connection.execute(text(f"CREATE ROLE {owner} NOSUPERUSER NOBYPASSRLS"))
        connection.execute(text(f"ALTER TABLE users OWNER TO {owner}"))
        connection.execute(text(f"ALTER FUNCTION sign_in_user(uuid, text, text) OWNER TO {owner}"))
    try:
        again = sign_in_user(database.app_api, sub=sub, email="owned@example.com")
    finally:
        with database.admin.begin() as connection:
            connection.execute(text("ALTER TABLE users OWNER TO CURRENT_USER"))
            connection.execute(
                text("ALTER FUNCTION sign_in_user(uuid, text, text) OWNER TO CURRENT_USER")
            )
            connection.execute(text(f"DROP ROLE {owner}"))

    assert (again.user_id, again.created) == (existing.user_id, False)
