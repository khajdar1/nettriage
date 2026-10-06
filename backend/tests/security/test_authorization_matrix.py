"""The authorization matrix (spec §6.4): every endpoint, called by an owner, an admin, an
analyst, a viewer, a non-member and an anonymous caller, against a hand-written table of
expected outcomes. Each case gets a fresh org, so destructive calls don't interfere."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from tenantdata import (
    add_analysis,
    add_finding,
    add_invitation,
    add_member,
    add_org,
    add_upload,
    add_user,
)

from nettriage.application.organizations import new_invitation_token, token_hash
from nettriage.entrypoints.api.services import Services

CALLERS = ("owner", "admin", "analyst", "viewer", "stranger", "anonymous")

# method, path, body; {org}, {target}, {invitation}, {org_name} and the rest are filled in per
# case.
ENDPOINTS: dict[str, tuple[str, str, dict[str, Any] | None]] = {
    "me": ("GET", "/api/v1/me", None),
    "create org": ("POST", "/api/v1/orgs", {"name": "New org"}),
    "read org": ("GET", "/api/v1/orgs/{org}", None),
    "rename org": ("PATCH", "/api/v1/orgs/{org}", {"name": "Renamed"}),
    "delete org": ("DELETE", "/api/v1/orgs/{org}?confirm_name={org_name}", None),
    "audit log": ("GET", "/api/v1/orgs/{org}/audit-log", None),
    "list members": ("GET", "/api/v1/orgs/{org}/members", None),
    "change role": ("PATCH", "/api/v1/orgs/{org}/members/{target}", {"role": "analyst"}),
    "remove member": ("DELETE", "/api/v1/orgs/{org}/members/{target}", None),
    "list invitations": ("GET", "/api/v1/orgs/{org}/invitations", None),
    "invite": (
        "POST",
        "/api/v1/orgs/{org}/invitations",
        {"email": "{new_email}", "role": "viewer"},
    ),
    "revoke invitation": ("DELETE", "/api/v1/orgs/{org}/invitations/{invitation}", None),
    "accept invitation": ("POST", "/api/v1/invitations/accept", {"token": "{token}"}),
    "create upload": (
        "POST",
        "/api/v1/orgs/{org}/uploads",
        {"filename": "flows.log", "size_bytes": 1024, "sha256": "{sha256}"},
    ),
    "list uploads": ("GET", "/api/v1/orgs/{org}/uploads", None),
    "read upload": ("GET", "/api/v1/orgs/{org}/uploads/{upload}", None),
    "list findings": ("GET", "/api/v1/orgs/{org}/findings", None),
    "read finding": ("GET", "/api/v1/orgs/{org}/findings/{finding}", None),
    "read technique": ("GET", "/api/v1/attack-techniques/{technique}", None),
    "triage finding": (
        "PATCH",
        "/api/v1/orgs/{org}/findings/{finding}",
        {"status": "investigating"},
    ),
    "comment on finding": (
        "POST",
        "/api/v1/orgs/{org}/findings/{finding}/comments",
        {"text": "Looks like our scanner."},
    ),
    "re-run AI explanation": (
        "POST",
        "/api/v1/orgs/{org}/findings/{finding}/ai-analyses",
        None,
    ),
    "rate AI explanation": (
        "PUT",
        "/api/v1/orgs/{org}/findings/{finding}/ai-analyses/{analysis}/feedback",
        {"feedback": "up"},
    ),
    "read AI usage": ("GET", "/api/v1/orgs/{org}/usage", None),
    "read the overview": ("GET", "/api/v1/orgs/{org}/overview", None),
}
# Headers an endpoint needs besides the session's: a triage change names the version it read.
EXTRA_HEADERS: dict[str, dict[str, str]] = {"triage finding": {"If-Match": '"1"'}}

# Expected status per caller: owner, admin, analyst, viewer, non-member, anonymous. The target
# member is another viewer; the pending invitation to accept is for the non-member's address.
MATRIX: dict[str, tuple[int, int, int, int, int, int]] = {
    "me": (200, 200, 200, 200, 200, 401),
    "create org": (201, 201, 201, 201, 201, 401),
    "read org": (200, 200, 200, 200, 404, 401),
    "rename org": (200, 200, 403, 403, 404, 401),
    "delete org": (204, 403, 403, 403, 404, 401),
    "audit log": (200, 200, 403, 403, 404, 401),
    "list members": (200, 200, 200, 200, 404, 401),
    "change role": (200, 200, 403, 403, 404, 401),
    "remove member": (204, 204, 403, 403, 404, 401),
    "list invitations": (200, 200, 403, 403, 404, 401),
    "invite": (201, 201, 403, 403, 404, 401),
    "revoke invitation": (204, 204, 403, 403, 404, 401),
    "accept invitation": (403, 403, 403, 403, 200, 401),
    "create upload": (201, 201, 201, 403, 404, 401),
    "list uploads": (200, 200, 200, 200, 404, 401),
    "read upload": (200, 200, 200, 200, 404, 401),
    "list findings": (200, 200, 200, 200, 404, 401),
    "read finding": (200, 200, 200, 200, 404, 401),
    "read technique": (200, 200, 200, 200, 200, 401),
    "triage finding": (200, 200, 200, 403, 404, 401),
    "comment on finding": (201, 201, 201, 403, 404, 401),
    "re-run AI explanation": (202, 202, 202, 403, 404, 401),
    "rate AI explanation": (200, 200, 200, 403, 404, 401),
    "read AI usage": (200, 200, 403, 403, 404, 401),
    "read the overview": (200, 200, 200, 200, 404, 401),
}


@dataclass
class World:
    values: dict[str, str]
    people: dict[str, UUID]


@pytest.fixture
def world(database: Database) -> World:
    stranger_email = f"{uuid4().hex}@example.com"
    token = new_invitation_token()
    with database.admin.begin() as connection:
        people = {
            name: add_user(connection) for name in ("owner", "admin", "analyst", "viewer", "target")
        }
        people["stranger"] = add_user(connection, stranger_email)
        org = add_org(connection, people["owner"])
        for name in ("owner", "admin", "analyst", "viewer"):
            add_member(connection, org, people[name], name)
        add_member(connection, org, people["target"], "viewer")
        invitation = add_invitation(connection, org, people["owner"], f"{uuid4().hex}@example.com")
        add_invitation(connection, org, people["owner"], stranger_email)
        upload = add_upload(connection, org, people["analyst"], status="analyzed")
        finding = add_finding(connection, org, upload)
        analysis = add_analysis(connection, org, finding)
        connection.exec_driver_sql(
            "UPDATE invitations SET token_hash = %s WHERE lower(email) = lower(%s)",
            (token_hash(token), stranger_email),
        )
    return World(
        values={
            "org": str(org),
            "org_name": "Org",
            "target": str(people["target"]),
            "invitation": str(invitation),
            "token": token,
            "new_email": f"{uuid4().hex}@example.com",
            "upload": str(upload),
            "finding": str(finding),
            "analysis": str(analysis),
            "technique": "T1595",
            "sha256": "ab" * 32,
        },
        people=people,
    )


def fill(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**values)
    if isinstance(value, dict):
        return {key: fill(item, values) for key, item in value.items()}
    return value


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
def test_every_endpoint_for_every_kind_of_caller(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    world: World,
    endpoint: str,
    caller: str,
) -> None:
    method, path, body = ENDPOINTS[endpoint]
    headers = {}
    if caller != "anonymous":
        headers = signed_in_as(database_client, services.sessions, world.people[caller], clock())
    headers |= EXTRA_HEADERS.get(endpoint, {})

    response = database_client.request(
        method,
        fill(path, world.values),
        json=fill(body, world.values),
        headers=headers,
        follow_redirects=False,
    )

    expected = MATRIX[endpoint][CALLERS.index(caller)]
    assert response.status_code == expected, response.text
    if expected >= 400:
        assert response.headers["content-type"] == "application/problem+json"


def test_the_matrix_covers_every_org_route(client: TestClient) -> None:
    from test_route_access import EXPECTED

    covered = {
        (
            method,
            fill(
                path.split("?")[0],
                {
                    "org": "{org_id}",
                    "target": "{user_id}",
                    "invitation": "{invitation_id}",
                    "upload": "{upload_id}",
                    "finding": "{finding_id}",
                    "analysis": "{analysis_id}",
                    "technique": "{technique_id}",
                },
            ),
        )
        for method, path, _ in ENDPOINTS.values()
    }
    routes = {key for key, access in EXPECTED.items() if key[1].startswith("/api/v1")}
    assert routes <= covered
