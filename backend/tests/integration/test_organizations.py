"""Organizations and members as the API's role (spec §5.2, §5.7, §6.4)."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from conftest import APP_API_PASSWORD, Database
from sqlalchemy import text
from tenantdata import add_member, add_user

from nettriage.adapters.organizations import (
    change_role,
    create_org,
    delete_org,
    get_org,
    list_members,
    remove_member,
    rename_org,
    role_of,
)
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.organizations import (
    ConfirmationMismatch,
    Forbidden,
    LastOwner,
    NotFound,
    QuotaExceeded,
    slugify,
)
from nettriage.application.permissions import Role


def new_user(database: Database) -> UUID:
    with database.admin.begin() as connection:
        return add_user(connection)


def org_with(database: Database, **roles: Role) -> tuple[UUID, dict[str, UUID]]:
    """An org created by a new owner, plus members with the given roles, keyed by name."""
    owner = new_user(database)
    org = create_org(database.app_api, user_id=owner, name=f"Org {uuid4().hex[:8]}").id
    people = {"owner": owner}
    with database.admin.begin() as connection:
        for name, role in roles.items():
            people[name] = add_user(connection)
            add_member(connection, org, people[name], role)
    return org, people


def test_creating_an_org_makes_the_user_its_owner(database: Database) -> None:
    user = new_user(database)
    name = f"Acme {uuid4().hex[:8]}"

    org = create_org(database.app_api, user_id=user, name=name)

    assert (org.name, org.slug, org.role, org.member_count) == (name, slugify(name), "owner", 1)
    assert role_of(database.app_api, org.id, user) == "owner"


def test_a_taken_slug_gets_a_random_suffix(database: Database) -> None:
    name = f"Twin {uuid4().hex[:8]}"
    first = create_org(database.app_api, user_id=new_user(database), name=name)

    second = create_org(database.app_api, user_id=new_user(database), name=name)

    assert second.slug.startswith(f"{first.slug}-")


def test_a_user_can_belong_to_at_most_three_orgs(database: Database) -> None:
    user = new_user(database)
    for number in range(3):
        create_org(database.app_api, user_id=user, name=f"Mine {number}")

    with pytest.raises(QuotaExceeded, match="at most 3"):
        create_org(database.app_api, user_id=user, name="One too many")


def test_three_parallel_creations_by_one_user_still_stop_at_three(database: Database) -> None:
    """The per-user advisory lock serializes the quota check."""
    user = new_user(database)
    url = database.url.set(username="app_api", password=APP_API_PASSWORD)
    engine = create_database_engine(url.render_as_string(hide_password=False), pool_size=5)

    def attempt(number: int) -> str:
        try:
            create_org(engine, user_id=user, name=f"Race {number}")
        except QuotaExceeded:
            return "refused"
        return "created"

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    engine.dispose()

    assert sorted(results) == ["created"] * 3 + ["refused"] * 2


def test_the_org_shows_the_callers_role_and_member_count(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")

    seen = get_org(database.app_api, org, people["viewer"])

    assert (seen.role, seen.member_count) == ("viewer", 2)


def test_renaming_keeps_the_slug(database: Database) -> None:
    org, people = org_with(database)
    before = get_org(database.app_api, org, people["owner"])

    after = rename_org(database.app_api, org, people["owner"], "New name")

    assert (after.name, after.slug) == ("New name", before.slug)


def test_deleting_needs_the_exact_name_and_takes_the_members_along(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")
    name = get_org(database.app_api, org, people["owner"]).name

    with pytest.raises(ConfirmationMismatch):
        delete_org(database.app_api, org, people["owner"], name.upper())
    delete_org(database.app_api, org, people["owner"], name)

    with database.admin.begin() as connection:
        left: int = connection.execute(
            text("SELECT count(*) FROM memberships WHERE org_id = :org"), {"org": org}
        ).scalar_one()
    assert left == 0
    assert role_of(database.app_api, org, people["viewer"]) is None


def test_members_are_listed_with_their_email_and_role(database: Database) -> None:
    org, people = org_with(database, analyst="analyst")

    members = list_members(database.app_api, org, people["analyst"])

    assert {(m.user_id, m.role) for m in members} == {
        (people["owner"], "owner"),
        (people["analyst"], "analyst"),
    }
    assert all(member.email.endswith("@example.com") for member in members)


def test_an_owner_can_give_any_role(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")

    previous, member = change_role(
        database.app_api,
        org,
        actor_id=people["owner"],
        actor_role="owner",
        target_id=people["viewer"],
        role="admin",
    )

    assert (previous, member.role) == ("viewer", "admin")


@pytest.mark.parametrize(
    ("target", "role", "allowed"),
    [("viewer", "analyst", True), ("viewer", "admin", False), ("other_admin", "viewer", False)],
)
def test_an_admin_manages_only_analysts_and_viewers(
    database: Database, target: str, role: Role, allowed: bool
) -> None:
    org, people = org_with(database, admin="admin", other_admin="admin", viewer="viewer")

    def change() -> None:
        change_role(
            database.app_api,
            org,
            actor_id=people["admin"],
            actor_role="admin",
            target_id=people[target],
            role=role,
        )

    if allowed:
        change()
    else:
        with pytest.raises(Forbidden):
            change()


def test_nobody_changes_their_own_role(database: Database) -> None:
    org, people = org_with(database)

    with pytest.raises(Forbidden, match="your own role"):
        change_role(
            database.app_api,
            org,
            actor_id=people["owner"],
            actor_role="owner",
            target_id=people["owner"],
            role="admin",
        )


def test_the_last_owner_can_not_be_demoted_but_one_of_two_can(database: Database) -> None:
    org, people = org_with(database, second="owner")
    change_role(
        database.app_api,
        org,
        actor_id=people["owner"],
        actor_role="owner",
        target_id=people["second"],
        role="admin",
    )

    with pytest.raises(LastOwner):
        change_role(
            database.app_api,
            org,
            actor_id=people["second"],
            actor_role="owner",
            target_id=people["owner"],
            role="viewer",
        )


def test_two_owners_demoting_each_other_at_once_leave_one_owner(database: Database) -> None:
    """Changes lock the org's row, so the second demotion sees the first."""
    org, people = org_with(database, second="owner")
    url = database.url.set(username="app_api", password=APP_API_PASSWORD)
    engine = create_database_engine(url.render_as_string(hide_password=False), pool_size=2)

    def demote(pair: tuple[str, str]) -> str:
        actor, target = pair
        try:
            change_role(
                engine,
                org,
                actor_id=people[actor],
                actor_role="owner",
                target_id=people[target],
                role="viewer",
            )
        except LastOwner:
            return "refused"
        return "demoted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, [("owner", "second"), ("second", "owner")]))
    engine.dispose()

    assert sorted(results) == ["demoted", "refused"]


def test_changing_someone_who_isnt_a_member_is_not_found(database: Database) -> None:
    org, people = org_with(database)

    with pytest.raises(NotFound):
        change_role(
            database.app_api,
            org,
            actor_id=people["owner"],
            actor_role="owner",
            target_id=new_user(database),
            role="viewer",
        )


def test_any_member_may_leave_but_not_the_last_owner(database: Database) -> None:
    org, people = org_with(database, viewer="viewer")

    left = remove_member(
        database.app_api,
        org,
        actor_id=people["viewer"],
        actor_role="viewer",
        target_id=people["viewer"],
    )

    assert (left.role, left.left) == ("viewer", True)
    with pytest.raises(LastOwner):
        remove_member(
            database.app_api,
            org,
            actor_id=people["owner"],
            actor_role="owner",
            target_id=people["owner"],
        )


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [
        ("admin", "analyst", True),
        ("admin", "other_admin", False),
        ("admin", "owner", False),
        ("analyst", "viewer", False),
        ("owner", "admin", True),
    ],
)
def test_removing_someone_else_needs_the_right_to_manage_them(
    database: Database, actor: str, target: str, allowed: bool
) -> None:
    org, people = org_with(
        database, admin="admin", other_admin="admin", analyst="analyst", viewer="viewer"
    )
    actor_role = role_of(database.app_api, org, people[actor])
    assert actor_role is not None

    def remove() -> None:
        remove_member(
            database.app_api,
            org,
            actor_id=people[actor],
            actor_role=actor_role,
            target_id=people[target],
        )

    if allowed:
        remove()
        assert role_of(database.app_api, org, people[target]) is None
    else:
        with pytest.raises(Forbidden):
            remove()


def test_a_non_member_has_no_role(database: Database) -> None:
    org, _ = org_with(database)

    assert role_of(database.app_api, org, new_user(database)) is None
