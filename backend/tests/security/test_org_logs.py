"""Inviting and accepting never write an email or an invitation token to the logs (spec §9.3),
even when a request is refused."""

import io
from uuid import uuid4

from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.services import Services


def test_inviting_and_accepting_log_no_email_and_no_token(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    logs: io.StringIO,
) -> None:
    email = f"{uuid4().hex}@example.com"
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        stranger = add_user(connection)
        invitee = add_user(connection, email)
    invitations = f"/api/v1/orgs/{org_id}/invitations"
    headers = signed_in_as(database_client, services.sessions, owner, clock())
    body = {"email": email, "role": "viewer"}
    created = database_client.post(invitations, json=body, headers=headers).json()
    token = str(created["invite_url"]).split("#")[1]
    again = database_client.post(invitations, json=body, headers=headers)
    stranger_headers = signed_in_as(database_client, services.sessions, stranger, clock())
    refused = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=stranger_headers
    )
    invitee_headers = signed_in_as(database_client, services.sessions, invitee, clock())
    joined = database_client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=invitee_headers
    )

    assert (again.status_code, refused.status_code, joined.status_code) == (409, 403, 200)
    lines = logs.getvalue().splitlines()
    assert lines
    leaked = [line for line in lines if email in line.lower() or token in line]
    assert leaked == []
