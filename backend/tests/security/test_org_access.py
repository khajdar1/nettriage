"""Org-scoped access (spec §6.4): membership plus permission, 404 for outsiders, 403 for
members without the permission, and every denial recorded."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID, uuid4

import httpx2
import pytest
from browser import signed_in_as
from conftest import Database, FakeClock, counter
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.access import OrgContext, OrgMember
from nettriage.entrypoints.api.services import Services

ROLES = ("owner", "admin", "analyst", "viewer")


@pytest.fixture
def probe(database_client: TestClient) -> TestClient:
    """The API with two probe routes: one needing org:update, one members:remove or self."""
    app: FastAPI = database_client.app  # type: ignore[assignment]

    @app.get("/api/v1/orgs/{org_id}/probe")
    def update_probe(
        org: Annotated[OrgContext, Depends(OrgMember("org:update"))],
    ) -> dict[str, str]:
        return {"role": org.role}

    @app.delete("/api/v1/orgs/{org_id}/probe/{user_id}", status_code=204)
    def remove_probe(
        org: Annotated[OrgContext, Depends(OrgMember("members:remove", or_self=True))],
    ) -> None:
        return None

    return database_client


@pytest.fixture
def org(database: Database) -> tuple[UUID, dict[str, UUID]]:
    with database.admin.begin() as connection:
        people = {role: add_user(connection) for role in (*ROLES, "stranger")}
        org_id = add_org(connection, people["owner"])
        for role in ROLES:
            add_member(connection, org_id, people[role], role)
    return org_id, people


@pytest.mark.parametrize(
    ("who", "status"),
    [("owner", 200), ("admin", 200), ("analyst", 403), ("viewer", 403), ("stranger", 404)],
)
def test_a_route_needs_a_membership_whose_role_has_the_permission(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, dict[str, UUID]],
    who: str,
    status: int,
) -> None:
    org_id, people = org
    signed_in_as(probe, services.sessions, people[who], clock())

    response = probe.get(f"/api/v1/orgs/{org_id}/probe")

    assert response.status_code == status
    if status != 200:
        assert response.headers["content-type"] == "application/problem+json"


def test_an_org_id_that_isnt_a_uuid_is_simply_not_found(
    probe: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, dict[str, UUID]]
) -> None:
    signed_in_as(probe, services.sessions, org[1]["owner"], clock())

    assert probe.get("/api/v1/orgs/not-an-id/probe").status_code == 404


def test_an_outsider_gets_the_same_answer_as_for_an_org_that_does_not_exist(
    probe: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, dict[str, UUID]]
) -> None:
    org_id, people = org
    signed_in_as(probe, services.sessions, people["stranger"], clock())

    existing = probe.get(f"/api/v1/orgs/{org_id}/probe")
    missing = probe.get(f"/api/v1/orgs/{uuid4()}/probe")

    def answer(response: httpx2.Response) -> tuple[int, object, object, object]:
        body = response.json()
        return response.status_code, body["type"], body["title"], body.get("detail")

    assert answer(existing) == answer(missing)
    assert existing.status_code == 404


def test_without_a_session_it_is_401(probe: TestClient, org: tuple[UUID, dict[str, UUID]]) -> None:
    assert probe.get(f"/api/v1/orgs/{org[0]}/probe").status_code == 401


def test_denials_are_counted_and_audited_without_writing_into_an_outsiders_target(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    metric_reader: InMemoryMetricReader,
    org: tuple[UUID, dict[str, UUID]],
) -> None:
    org_id, people = org
    for who in ("viewer", "stranger"):
        signed_in_as(probe, services.sessions, people[who], clock())
        probe.get(f"/api/v1/orgs/{org_id}/probe")

    assert counter(metric_reader, "nettriage.authz.denied") == 2
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT actor_user_id, org_id, target_id, details FROM audit_log "
                "WHERE action = 'authz.denied' AND target_id = :org ORDER BY created_at"
            ),
            {"org": str(org_id)},
        ).all()
    assert [(row.actor_user_id, row.org_id) for row in rows] == [
        (people["viewer"], org_id),
        (people["stranger"], None),
    ]
    assert rows[0].details == {
        "permission": "org:update",
        "route": "/api/v1/orgs/{org_id}/probe",
        "reason": "role",
    }
    assert rows[1].details["reason"] == "not_a_member"


def test_a_member_may_act_on_themselves_where_a_route_allows_it(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    org: tuple[UUID, dict[str, UUID]],
) -> None:
    org_id, people = org
    headers = signed_in_as(probe, services.sessions, people["viewer"], clock())

    own = probe.delete(f"/api/v1/orgs/{org_id}/probe/{people['viewer']}", headers=headers)
    other = probe.delete(f"/api/v1/orgs/{org_id}/probe/{people['analyst']}", headers=headers)
    anyone = probe.delete(f"/api/v1/orgs/{org_id}/probe/{uuid4()}", headers=headers)

    assert (own.status_code, other.status_code, anyone.status_code) == (204, 403, 403)


def test_repeated_denials_by_one_caller_write_one_audit_row_a_minute(
    probe: TestClient,
    services: Services,
    clock: FakeClock,
    database: Database,
    metric_reader: InMemoryMetricReader,
    org: tuple[UUID, dict[str, UUID]],
) -> None:
    """Anyone can sign up, so an audit row for every denial would let one account fill the
    database; the metric still counts each one."""
    org_id, people = org
    signed_in_as(probe, services.sessions, people["stranger"], clock())
    for _ in range(10):
        probe.get(f"/api/v1/orgs/{uuid4()}/probe")
    clock.advance(timedelta(minutes=1))
    probe.get(f"/api/v1/orgs/{org_id}/probe")

    assert counter(metric_reader, "nettriage.authz.denied") == 11
    with database.admin.begin() as connection:
        rows: int = connection.execute(
            text(
                "SELECT count(*) FROM audit_log "
                "WHERE action = 'authz.denied' AND actor_user_id = :actor"
            ),
            {"actor": people["stranger"]},
        ).scalar_one()
    assert rows == 2
