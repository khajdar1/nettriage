"""Triage through the API (spec §7): a finding's status and assignee change only with `If-Match`
naming the version the caller read, comments join its history, and each is audited."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_finding, add_member, add_org, add_upload, add_user

from nettriage.entrypoints.api.services import Services


@pytest.fixture
def world(database: Database) -> dict[str, UUID]:
    """An org with an owner, an analyst and a viewer, and one open finding."""
    with database.admin.begin() as connection:
        owner, analyst, viewer = (add_user(connection) for _ in range(3))
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        add_member(connection, org, analyst, "analyst")
        add_member(connection, org, viewer, "viewer")
        finding = add_finding(connection, org, add_upload(connection, org, owner, "analyzed"))
    return {"org": org, "owner": owner, "analyst": analyst, "viewer": viewer, "finding": finding}


@pytest.fixture
def headers(
    database_client: TestClient, services: Services, clock: FakeClock, world: dict[str, UUID]
) -> dict[str, str]:
    return signed_in_as(database_client, services.sessions, world["analyst"], clock())


def url(world: dict[str, UUID], suffix: str = "") -> str:
    return f"/api/v1/orgs/{world['org']}/findings/{world['finding']}{suffix}"


def patch(
    client: TestClient,
    world: dict[str, UUID],
    headers: dict[str, str],
    body: dict[str, Any],
    if_match: str | None = '"1"',
) -> Any:
    sent = headers if if_match is None else {**headers, "If-Match": if_match}
    return client.patch(url(world), json=body, headers=sent)


def audited(database: Database, org: UUID) -> list[tuple[str, dict[str, Any]]]:
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT action, details FROM audit_log WHERE org_id = :org AND action LIKE "
                "'finding.%' ORDER BY created_at, id"
            ),
            {"org": org},
        ).all()
    return [(row.action, row.details) for row in rows]


def test_a_status_change_returns_the_finding_with_its_new_etag(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    etag = database_client.get(url(world)).headers["etag"]

    response = patch(database_client, world, headers, {"status": "investigating"}, etag)

    assert response.status_code == 200, response.text
    assert response.headers["etag"] == '"2"'
    finding = response.json()
    assert (finding["status"], finding["version"]) == ("investigating", 2)
    assert [(event["type"], event["payload"]) for event in finding["events"]][-1] == (
        "status_changed",
        {"from": "open", "to": "investigating"},
    )


def test_a_change_without_if_match_is_refused_with_428(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    response = patch(database_client, world, headers, {"status": "resolved"}, if_match=None)

    assert response.status_code == 428
    assert response.headers["content-type"] == "application/problem+json"
    assert "If-Match" in response.json()["detail"]


@pytest.mark.parametrize("if_match", ["*", 'W/"1"', "1", '"1", "2"'])
def test_an_if_match_that_names_no_version_is_refused_with_428(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID], if_match: str
) -> None:
    response = patch(database_client, world, headers, {"status": "resolved"}, if_match)

    assert response.status_code == 428


def test_a_stale_etag_gets_412_with_the_current_one_and_changes_nothing(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    patch(database_client, world, headers, {"status": "investigating"})

    stale = patch(database_client, world, headers, {"status": "false_positive"})

    assert stale.status_code == 412
    assert stale.headers["etag"] == '"2"'
    assert "changed since you read it" in stale.json()["detail"]
    assert database_client.get(url(world)).json()["status"] == "investigating"


def test_a_finding_is_assigned_and_unassigned(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    assigned = patch(database_client, world, headers, {"assignee_id": str(world["owner"])})
    unassigned = patch(database_client, world, headers, {"assignee_id": None}, '"2"')

    assert assigned.json()["assignee_id"] == str(world["owner"])
    assert (unassigned.json()["assignee_id"], unassigned.headers["etag"]) == (None, '"3"')


def test_a_viewer_can_not_be_the_assignee(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    response = patch(database_client, world, headers, {"assignee_id": str(world["viewer"])})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Assign the finding to an owner, admin or analyst of this organization."
    )


@pytest.mark.parametrize(
    "body",
    [{}, {"status": None}, {"status": "closed"}, {"severity": "low"}, {"assignee_id": "someone"}],
)
def test_a_change_that_says_nothing_valid_is_a_422(
    database_client: TestClient,
    headers: dict[str, str],
    world: dict[str, UUID],
    body: dict[str, Any],
) -> None:
    response = patch(database_client, world, headers, body)

    assert response.status_code == 422


def test_a_comment_joins_the_history_once_per_idempotency_key(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID]
) -> None:
    keyed = {**headers, "Idempotency-Key": "comment-1"}
    body = {"text": "  Our weekly scanner.\nSafe to close.  "}

    first = database_client.post(url(world, "/comments"), json=body, headers=keyed)
    retry = database_client.post(url(world, "/comments"), json=body, headers=keyed)

    assert first.status_code == 201, first.text
    assert retry.json() == first.json()
    comment = first.json()
    assert (comment["type"], comment["actor_id"]) == ("commented", str(world["analyst"]))
    assert comment["payload"] == {"text": "Our weekly scanner.\nSafe to close."}
    detail = database_client.get(url(world))
    assert [event["type"] for event in detail.json()["events"]] == ["created", "commented"]
    assert detail.json()["events_total"] == 2
    assert detail.headers["etag"] == '"1"'


@pytest.mark.parametrize("text_", ["", "   ", "x" * 2001, "a\x00b", "bell\x07"])
def test_a_comment_is_1_to_2000_characters_without_control_characters(
    database_client: TestClient, headers: dict[str, str], world: dict[str, UUID], text_: str
) -> None:
    response = database_client.post(url(world, "/comments"), json={"text": text_}, headers=headers)

    assert response.status_code == 422


def test_triage_and_comments_are_audited_without_the_comments_text(
    database_client: TestClient,
    headers: dict[str, str],
    world: dict[str, UUID],
    database: Database,
) -> None:
    patch(
        database_client,
        world,
        headers,
        {"status": "resolved", "assignee_id": str(world["analyst"])},
    )
    database_client.post(
        url(world, "/comments"), json={"text": "Private note 7f3a"}, headers=headers
    )

    assert audited(database, world["org"]) == [
        ("finding.status_changed", {"from": "open", "to": "resolved"}),
        ("finding.assigned", {"from": None, "to": str(world["analyst"])}),
        ("finding.commented", {}),
    ]


def test_an_org_may_triage_50_times_at_once_and_500_a_day(
    database_client: TestClient,
    headers: dict[str, str],
    world: dict[str, UUID],
    clock: FakeClock,
) -> None:
    """Comments and changes share the org's `triage.org` quota, so no member can fill the shared
    database with history (the owner's decision, Plan 4c). Two seconds between requests keep
    the member's own `api.mutation.user` limit out of the way."""
    statuses = []
    for n in range(51):
        clock.advance(timedelta(seconds=2))
        response = database_client.post(
            url(world, "/comments"), json={"text": f"note {n}"}, headers=headers
        )
        statuses.append(response.status_code)
    clock.advance(timedelta(seconds=2))
    change = patch(database_client, world, headers, {"status": "resolved"})

    assert statuses == [201] * 50 + [429]
    assert response.json()["detail"]
    assert int(response.headers["retry-after"]) > 0
    assert change.status_code == 429
