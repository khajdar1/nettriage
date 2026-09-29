"""No change runs on a role the caller has lost (spec §6.4, no escalation). OrgMember reads the
caller's role before the change's transaction starts, and a demotion or a removal can commit in
between. Here OrgMember is made to see the role the caller had a moment ago; the change must
decide with the role they have now, under the org's lock."""

from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_invitation, add_member, add_org, add_user

from nettriage.entrypoints.api import access
from nettriage.entrypoints.api.services import Services

NAME = "Stale Roles Inc"


def snapshot(database: Database, org: UUID) -> tuple[Any, ...]:
    """Everything a change could touch: the name, the members and their roles, the pending
    invitations and the uploads."""
    with database.admin.begin() as connection:
        name = connection.execute(
            text("SELECT name FROM organizations WHERE id = :org"), {"org": org}
        ).scalar_one_or_none()
        members = connection.execute(
            text("SELECT user_id, role FROM memberships WHERE org_id = :org ORDER BY user_id"),
            {"org": org},
        ).all()
        invitations = connection.execute(
            text(
                "SELECT id FROM invitations WHERE org_id = :org AND revoked_at IS NULL ORDER BY id"
            ),
            {"org": org},
        ).all()
        uploads = connection.execute(
            text("SELECT id FROM uploads WHERE org_id = :org ORDER BY id"), {"org": org}
        ).all()
    return name, tuple(members), tuple(invitations), tuple(uploads)


@pytest.mark.parametrize(
    ("change", "then", "now", "status"),
    [
        ("rename", "admin", "viewer", 403),
        ("delete", "owner", "admin", 403),
        ("promote", "owner", "viewer", 403),
        ("remove", "admin", "viewer", 403),
        ("invite", "admin", None, 404),
        ("revoke", "admin", None, 404),
        ("upload", "analyst", "viewer", 403),
    ],
)
def test_a_change_uses_the_role_the_caller_has_now(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    then: str,
    now: str | None,
    status: int,
) -> None:
    with database.admin.begin() as connection:
        owner, actor, analyst = (add_user(connection) for _ in range(3))
        org = add_org(connection, owner)
        connection.execute(
            text("UPDATE organizations SET name = :name WHERE id = :org"),
            {"name": NAME, "org": org},
        )
        add_member(connection, org, owner, "owner")
        add_member(connection, org, analyst, "analyst")
        if now is not None:
            add_member(connection, org, actor, now)
        invitation = add_invitation(connection, org, owner, f"{uuid4().hex}@example.com")
    monkeypatch.setattr(access, "role_of", lambda engine, org_id, user_id: then)
    headers = signed_in_as(database_client, services.sessions, actor, clock())
    requests: dict[str, tuple[str, str, dict[str, Any] | None]] = {
        "rename": ("PATCH", f"/api/v1/orgs/{org}", {"name": "Renamed"}),
        "delete": ("DELETE", f"/api/v1/orgs/{org}?confirm_name={quote(NAME)}", None),
        "promote": ("PATCH", f"/api/v1/orgs/{org}/members/{analyst}", {"role": "owner"}),
        "remove": ("DELETE", f"/api/v1/orgs/{org}/members/{analyst}", None),
        "invite": (
            "POST",
            f"/api/v1/orgs/{org}/invitations",
            {"email": f"{uuid4().hex}@example.com", "role": "viewer"},
        ),
        "revoke": ("DELETE", f"/api/v1/orgs/{org}/invitations/{invitation}", None),
        "upload": (
            "POST",
            f"/api/v1/orgs/{org}/uploads",
            {"filename": "flows.log", "size_bytes": 1, "sha256": "0" * 64},
        ),
    }
    method, path, body = requests[change]
    before = snapshot(database, org)

    response = database_client.request(method, path, json=body, headers=headers)

    assert response.status_code == status
    assert snapshot(database, org) == before
