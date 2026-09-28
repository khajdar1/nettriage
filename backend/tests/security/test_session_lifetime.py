"""Session expiry and failure handling (spec §5.5, §6.2)."""

from datetime import timedelta
from uuid import uuid4

from browser import sign_in
from conftest import FakeClock, RuntimeTable
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient

from nettriage.application.sessions import COOKIE_NAME, session_key


def stored_last_seen(table: RuntimeTable, session_id: str) -> str:
    item = table.client.get_item(
        TableName=table.name, Key={"pk": {"S": f"SESSION#{session_key(session_id)}"}}
    )["Item"]
    return item["last_seen_at"]["N"]


def test_a_session_idle_for_sixty_minutes_is_ended(
    database_client: TestClient, idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    clock.advance(timedelta(minutes=60))

    assert database_client.get("/api/v1/me").status_code == 401

    clock.advance(timedelta(minutes=-30))  # the session was deleted, not just refused
    assert database_client.get("/api/v1/me").status_code == 401


def test_activity_keeps_a_session_alive_but_not_past_twelve_hours(
    database_client: TestClient, idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    for _ in range(23):  # every 30 minutes for 11.5 hours
        clock.advance(timedelta(minutes=30))
        assert database_client.get("/api/v1/me").status_code == 200

    clock.advance(timedelta(minutes=30))

    assert database_client.get("/api/v1/me").status_code == 401


def test_activity_is_written_at_most_every_five_minutes(
    database_client: TestClient,
    idp: FakeIdentityProvider,
    clock: FakeClock,
    runtime_table: RuntimeTable,
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    session_id = database_client.cookies[COOKIE_NAME]
    first = stored_last_seen(runtime_table, session_id)

    clock.advance(timedelta(minutes=4))
    database_client.get("/api/v1/me")
    unchanged = stored_last_seen(runtime_table, session_id)
    clock.advance(timedelta(minutes=1))
    database_client.get("/api/v1/me")

    assert unchanged == first
    assert stored_last_seen(runtime_table, session_id) != first


def test_an_unreadable_session_store_fails_closed(
    database_client: TestClient, idp: FakeIdentityProvider, runtime_table: RuntimeTable
) -> None:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    runtime_table.client.delete_table(TableName=runtime_table.name)

    response = database_client.get("/api/v1/me")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
