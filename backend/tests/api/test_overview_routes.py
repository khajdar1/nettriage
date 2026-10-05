"""An organization's overview (Plan 6d): its findings by severity and status, what's unassigned
and what's the caller's, what's new, its members and its last upload."""

from __future__ import annotations

from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.entrypoints.api.services import Services

ZEROS = {"critical": 0, "high": 0, "medium": 0, "low": 0}


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID, UUID]:
    """An org, its owner, and an analyst in it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        analyst = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
        add_member(connection, org_id, analyst, "analyst")
    return org_id, owner, analyst


def finding(
    database: Database,
    org: UUID,
    upload: UUID,
    *,
    severity: str = "high",
    status: str = "open",
    assignee: UUID | None = None,
    created_at: str | None = None,
) -> None:
    with database.admin.begin() as connection:
        finding_id = add_finding(connection, org, upload, severity=severity)
        connection.execute(
            text(
                "UPDATE findings SET status = :status, assignee_id = :assignee, "
                "created_at = COALESCE(CAST(:created AS timestamptz), created_at) WHERE id = :id"
            ),
            {"status": status, "assignee": assignee, "created": created_at, "id": finding_id},
        )


def overview(
    client: TestClient, services: Services, clock: FakeClock, org: UUID, user: UUID
) -> dict[str, object]:
    signed_in_as(client, services.sessions, user, clock())
    response = client.get(f"/api/v1/orgs/{org}/overview")
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_an_organization_overview_counts_its_findings(
    database_client: TestClient,
    database: Database,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    org_id, owner, analyst = org
    with database.admin.begin() as connection:
        older = add_upload(connection, org_id, owner, status="analyzed")
        newest = add_upload(connection, org_id, owner, status="analyzed")
    finding(database, org_id, older, severity="critical", assignee=owner)
    finding(database, org_id, older, severity="high", status="investigating", assignee=analyst)
    finding(database, org_id, older, severity="high", created_at="2026-09-01T00:00:00Z")
    finding(database, org_id, older, severity="low", status="resolved", assignee=owner)
    finding(database, org_id, newest, severity="medium", status="false_positive")

    seen = overview(database_client, services, clock, org_id, owner)

    assert seen["unresolved_by_severity"] == {**ZEROS, "critical": 1, "high": 2}
    assert seen["by_status"] == {
        "open": 2,
        "investigating": 1,
        "resolved": 1,
        "false_positive": 1,
    }
    assert (seen["unresolved_unassigned"], seen["unresolved_mine"]) == (1, 1)
    assert seen["new_last_day"] == 4
    assert seen["member_count"] == 2
    last = seen["last_upload"]
    assert isinstance(last, dict)
    assert (last["id"], last["findings"], last["worst_severity"]) == (str(newest), 1, "medium")


def test_what_is_mine_depends_on_who_asks(
    database_client: TestClient,
    database: Database,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    org_id, owner, analyst = org
    with database.admin.begin() as connection:
        upload = add_upload(connection, org_id, owner, status="analyzed")
    finding(database, org_id, upload, assignee=analyst)
    finding(database, org_id, upload, status="investigating", assignee=analyst)

    assert overview(database_client, services, clock, org_id, owner)["unresolved_mine"] == 0
    assert overview(database_client, services, clock, org_id, analyst)["unresolved_mine"] == 2


def test_a_new_organization_has_nothing_to_count(
    database_client: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    seen = overview(database_client, services, clock, org[0], org[1])

    assert seen == {
        "unresolved_by_severity": ZEROS,
        "by_status": {"open": 0, "investigating": 0, "resolved": 0, "false_positive": 0},
        "unresolved_unassigned": 0,
        "unresolved_mine": 0,
        "new_last_day": 0,
        "member_count": 2,
        "last_upload": None,
    }


def test_an_overview_is_only_for_the_organizations_members(
    database_client: TestClient,
    database: Database,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, UUID, UUID],
) -> None:
    with database.admin.begin() as connection:
        stranger = add_user(connection)
    signed_in_as(database_client, services.sessions, stranger, clock())

    response = database_client.get(f"/api/v1/orgs/{org[0]}/overview")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
