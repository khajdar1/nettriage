"""An org's AI usage through the API (spec §7): calls, tokens and cost per UTC day, newest first,
for Owners and Admins. The triage worker counts every model call (Plan 5c)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from browser import signed_in_as
from conftest import Database, FakeClock
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org, add_user

from nettriage.entrypoints.api.services import Services


@pytest.fixture
def org(database: Database) -> tuple[UUID, UUID]:
    """An org and its owner, with no AI use yet."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org_id = add_org(connection, owner)
        add_member(connection, org_id, owner, "owner")
    return org_id, owner


@pytest.fixture
def signed_in(
    database_client: TestClient, services: Services, clock: FakeClock, org: tuple[UUID, UUID]
) -> TestClient:
    signed_in_as(database_client, services.sessions, org[1], clock())
    return database_client


def used(database: Database, org_id: UUID, days_ago: int, calls: int, cost: str) -> None:
    with database.admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_usage (org_id, day, calls, input_tokens, output_tokens, cost_usd) "
                "VALUES (:org, (now() AT TIME ZONE 'UTC')::date - :ago, :calls, :calls * 1800, "
                ":calls * 320, CAST(:cost AS numeric))"
            ),
            {"org": org_id, "ago": days_ago, "calls": calls, "cost": cost},
        )


def day(days_ago: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()


def usage(client: TestClient, org: tuple[UUID, UUID], **params: Any) -> Any:
    return client.get(f"/api/v1/orgs/{org[0]}/usage", params=params)


def test_ai_spend_is_listed_by_day_newest_first_with_totals(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID]
) -> None:
    used(database, org[0], 3, calls=2, cost="0.000444")
    used(database, org[0], 0, calls=1, cost="0.000222")
    used(database, org[0], 40, calls=5, cost="0.001110")

    response = usage(signed_in, org)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "days": [
            {
                "day": day(0),
                "calls": 1,
                "input_tokens": 1800,
                "output_tokens": 320,
                "cost_usd": "0.000222",
            },
            {
                "day": day(3),
                "calls": 2,
                "input_tokens": 3600,
                "output_tokens": 640,
                "cost_usd": "0.000444",
            },
        ],
        "totals": {
            "calls": 3,
            "input_tokens": 5400,
            "output_tokens": 960,
            "cost_usd": "0.000666",
        },
    }


def test_days_narrows_the_window(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID]
) -> None:
    used(database, org[0], 0, calls=1, cost="0.000222")
    used(database, org[0], 1, calls=1, cost="0.000222")

    response = usage(signed_in, org, days=1)

    assert [entry["day"] for entry in response.json()["days"]] == [day(0)]


@pytest.mark.parametrize("days", [0, 91, "a week"])
def test_days_outside_1_to_90_is_a_422(
    signed_in: TestClient, org: tuple[UUID, UUID], days: Any
) -> None:
    assert usage(signed_in, org, days=days).status_code == 422


def test_an_org_without_ai_use_has_none(signed_in: TestClient, org: tuple[UUID, UUID]) -> None:
    assert usage(signed_in, org).json() == {
        "days": [],
        "totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"},
    }


def test_another_orgs_usage_is_never_counted(
    signed_in: TestClient, database: Database, org: tuple[UUID, UUID]
) -> None:
    with database.admin.begin() as connection:
        stranger = add_user(connection)
        theirs = add_org(connection, stranger)
    used(database, theirs, 0, calls=9, cost="0.001998")

    assert usage(signed_in, org).json()["totals"]["calls"] == 0
