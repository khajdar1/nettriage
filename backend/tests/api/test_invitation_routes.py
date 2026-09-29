"""Invitations through the API (spec §6.3, §6.5, §7)."""

from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fake_idp import APP_ORIGIN
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.application.organizations import token_hash
from nettriage.entrypoints.api.services import Services


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID]:
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
    return org_id, owner


def invite(
    client: TestClient, headers: dict[str, str], org: UUID, email: str, role: str = "viewer"
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/orgs/{org}/invitations", json={"email": email, "role": role}, headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_an_invitation_link_carries_the_token_in_its_fragment_only_once(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())

    created = invite(database_client, headers, org[0], "Invitee@Example.com", "analyst")
    listed = database_client.get(f"/api/v1/orgs/{org[0]}/invitations").json()["invitations"]

    url = str(created["invite_url"])
    token = url.removeprefix(f"{APP_ORIGIN}/invite#")
    assert url.startswith(f"{APP_ORIGIN}/invite#")
    assert len(token) == 43
    assert [i["email"] for i in listed] == ["Invitee@Example.com"]
    assert token not in str(listed)
    with database.admin.begin() as connection:
        stored: str = connection.execute(
            text("SELECT token_hash FROM invitations WHERE org_id = :org"), {"org": org[0]}
        ).scalar_one()
    assert stored == token_hash(token)


@pytest.mark.parametrize("email", ["not-an-address", "a b@example.com", ""])
def test_an_invitation_needs_an_email_address(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    email: str,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())

    response = database_client.post(
        f"/api/v1/orgs/{org[0]}/invitations",
        json={"email": email, "role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 422


def test_inviting_the_same_address_twice_is_a_conflict(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    invite(database_client, headers, org[0], "twice@example.com")

    again = database_client.post(
        f"/api/v1/orgs/{org[0]}/invitations",
        json={"email": "TWICE@example.com", "role": "viewer"},
        headers=headers,
    )

    assert again.status_code == 409


def test_invitations_are_rate_limited_per_org(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    statuses = [
        database_client.post(
            f"/api/v1/orgs/{org[0]}/invitations",
            json={"email": f"{uuid4().hex}@example.com", "role": "viewer"},
            headers=headers,
        ).status_code
        for _ in range(6)
    ]

    assert statuses == [201] * 5 + [429]


def test_a_revoked_invitation_disappears_and_can_not_be_revoked_again(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    created = invite(database_client, headers, org[0], "gone@example.com")
    invitation_id = created["invitation"]["id"]  # type: ignore[index]

    first = database_client.delete(
        f"/api/v1/orgs/{org[0]}/invitations/{invitation_id}", headers=headers
    )
    again = database_client.delete(
        f"/api/v1/orgs/{org[0]}/invitations/{invitation_id}", headers=headers
    )

    assert (first.status_code, again.status_code) == (204, 404)
    assert database_client.get(f"/api/v1/orgs/{org[0]}/invitations").json()["invitations"] == []


def test_an_admin_can_not_revoke_an_invitation_for_a_role_they_can_not_grant(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    """Admins manage analysts and viewers only (spec §6.4), so an owner's invitation of a new
    admin isn't theirs to cancel."""
    owner_headers = signed_in_as(database_client, services.sessions, org[1], clock())
    created = invite(database_client, owner_headers, org[0], f"{uuid4().hex}@example.com", "admin")
    invitation_id = created["invitation"]["id"]  # type: ignore[index]
    with database.admin.begin() as connection:
        admin = add_user(connection)
        add_member(connection, org[0], admin, "admin")
    admin_headers = signed_in_as(database_client, services.sessions, admin, clock())

    response = database_client.delete(
        f"/api/v1/orgs/{org[0]}/invitations/{invitation_id}", headers=admin_headers
    )

    assert response.status_code == 403
    owner_headers = signed_in_as(database_client, services.sessions, org[1], clock())
    listed = database_client.get(f"/api/v1/orgs/{org[0]}/invitations").json()["invitations"]
    assert [invitation["id"] for invitation in listed] == [invitation_id]


def test_accepting_joins_the_org_once(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    email = f"{uuid4().hex}@example.com"
    token = str(invite(database_client, headers, org[0], email, "analyst")["invite_url"]).split(
        "#"
    )[1]
    with database.admin.begin() as connection:
        invitee = add_user(connection, email.upper())
    invitee_headers = signed_in_as(database_client, services.sessions, invitee, clock())

    joined = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )
    again = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )

    assert (joined.status_code, joined.json()["role"], joined.json()["id"]) == (
        200,
        "analyst",
        str(org[0]),
    )
    assert again.status_code == 404
    with database.admin.begin() as connection:
        joins: int = connection.execute(
            text("SELECT count(*) FROM audit_log WHERE org_id = :org AND action = 'member.joined'"),
            {"org": org[0]},
        ).scalar_one()
    assert joins == 1


def test_an_invitation_for_someone_else_is_refused(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    database: Database,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())
    email = f"{uuid4().hex}@example.com"
    token = str(invite(database_client, headers, org[0], email)["invite_url"]).split("#")[1]
    with database.admin.begin() as connection:
        other = add_user(connection)
        invitee = add_user(connection, email)
    other_headers = signed_in_as(database_client, services.sessions, other, clock())

    refused = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=other_headers
    )
    invitee_headers = signed_in_as(database_client, services.sessions, invitee, clock())
    joined = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )

    assert refused.status_code == 403
    assert joined.status_code == 200


@pytest.mark.parametrize("token", ["", "short", "x" * 43, "é" * 43])
def test_a_token_that_was_never_issued_is_not_found(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID],
    token: str,
) -> None:
    headers = signed_in_as(database_client, services.sessions, org[1], clock())

    response = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=headers
    )

    assert response.status_code == 404
