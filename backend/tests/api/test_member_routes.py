"""Members through the API (spec §6.4, §7): list, change roles, remove, leave."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.services import Services


@dataclass
class Team:
    org: UUID
    people: dict[str, UUID]


@pytest.fixture
def team(database: Database) -> Team:
    with database.admin.begin() as connection:
        people = {name: add_user(connection) for name in ("owner", "admin", "analyst", "viewer")}
        org = add_org(connection, people["owner"])
        for name, user in people.items():
            add_member(connection, org, user, name)
    return Team(org=org, people=people)


def act_as(client: TestClient, services: Services, clock: FakeClock, user: UUID) -> dict[str, str]:
    return signed_in_as(client, services.sessions, user, clock())


def last_audit(database: Database, org: UUID) -> tuple[str, dict[str, object]]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT action, details FROM audit_log WHERE org_id = :org "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"org": org},
        ).one()
    return row.action, row.details


def test_members_are_listed_with_email_and_role(
    database_client: TestClient, services: Services, clock: FakeClock, team: Team
) -> None:
    act_as(database_client, services, clock, team.people["viewer"])

    response = database_client.get(f"/api/v1/orgs/{team.org}/members")

    assert response.status_code == 200
    roles = {m["user_id"]: m["role"] for m in response.json()["members"]}
    assert roles == {str(user): name for name, user in team.people.items()}


def test_an_owner_changes_a_role_and_it_is_audited(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["owner"])

    response = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{team.people['viewer']}",
        json={"role": "admin"},
        headers=headers,
    )

    assert (response.status_code, response.json()["role"]) == (200, "admin")
    assert last_audit(database, team.org) == (
        "member.role_changed",
        {"from": "viewer", "to": "admin"},
    )


def test_an_admin_granting_admin_is_an_audited_denial(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["admin"])

    response = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{team.people['viewer']}",
        json={"role": "admin"},
        headers=headers,
    )

    assert response.status_code == 403
    action, details = last_audit(database, team.org)
    assert (action, details["reason"], details["permission"]) == (
        "authz.denied",
        "escalation",
        "members:role",
    )


def test_nobody_changes_their_own_role_and_the_last_owner_stays(
    database_client: TestClient, services: Services, clock: FakeClock, team: Team
) -> None:
    headers = act_as(database_client, services, clock, team.people["owner"])

    own = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{team.people['owner']}",
        json={"role": "viewer"},
        headers=headers,
    )
    leave = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['owner']}", headers=headers
    )

    assert own.status_code == 403
    assert leave.status_code == 409
    assert "at least one owner" in leave.json()["detail"]


def test_a_viewer_can_leave(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["viewer"])

    response = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['viewer']}", headers=headers
    )

    assert response.status_code == 204
    assert last_audit(database, team.org) == ("member.left", {"role": "viewer"})
    assert database_client.get(f"/api/v1/orgs/{team.org}").status_code == 404


def test_an_admin_removes_an_analyst_but_not_the_owner(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["admin"])

    removed = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['analyst']}", headers=headers
    )
    audited = last_audit(database, team.org)
    refused = database_client.delete(
        f"/api/v1/orgs/{team.org}/members/{team.people['owner']}", headers=headers
    )

    assert (removed.status_code, refused.status_code) == (204, 403)
    assert audited == ("member.removed", {"role": "analyst"})


def test_changing_someone_who_isnt_a_member_is_not_found(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    team: Team,
    database: Database,
) -> None:
    headers = act_as(database_client, services, clock, team.people["owner"])
    with database.admin.begin() as connection:
        stranger = add_user(connection)

    response = database_client.patch(
        f"/api/v1/orgs/{team.org}/members/{stranger}", json={"role": "viewer"}, headers=headers
    )

    assert response.status_code == 404
