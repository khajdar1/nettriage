"""Bogus session cookies must not spend the runtime table's read capacity (final review of Plan
3b). The edge only checks that a `__Host-session` cookie exists, so without these guards a loop
of `Cookie: __Host-session=x` requests would cost a strongly consistent read each, until real
users' session checks were throttled into 503s."""

import secrets
from dataclasses import replace

from conftest import CountingClient, RuntimeTable
from fastapi.testclient import TestClient

from nettriage.adapters.sessions import SessionStore
from nettriage.application.sessions import COOKIE_NAME
from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings


def counted_client(
    settings: Settings, services: Services, table: RuntimeTable
) -> tuple[TestClient, CountingClient]:
    dynamodb = CountingClient(table.client)
    store = SessionStore(dynamodb, table.name)  # type: ignore[arg-type]
    app = create_app(settings, replace(services, sessions=store))
    return TestClient(app, base_url="https://app.test"), dynamodb


def viewer(ip: str) -> dict[str, str]:
    return {"CloudFront-Viewer-Address": f"{ip}:4444"}


def test_a_cookie_that_cannot_be_a_session_id_is_refused_without_a_read(
    settings: Settings, services: Services, runtime_table: RuntimeTable
) -> None:
    client, dynamodb = counted_client(settings, services, runtime_table)

    for value in ("x", "a" * 42, "a" * 44, "!" * 43):
        client.cookies.set(COOKIE_NAME, value, domain="app.test")
        assert client.get("/api/v1/me", headers=viewer("198.51.100.1")).status_code == 401

    assert dynamodb.calls["get_item"] == 0


def test_an_ip_that_keeps_presenting_unknown_sessions_stops_costing_reads(
    settings: Settings, services: Services, runtime_table: RuntimeTable
) -> None:
    client, dynamodb = counted_client(settings, services, runtime_table)

    for _ in range(20):
        client.cookies.set(COOKIE_NAME, secrets.token_urlsafe(32), domain="app.test")
        assert client.get("/api/v1/me", headers=viewer("198.51.100.2")).status_code == 401
    flooded = dynamodb.calls["get_item"]
    client.cookies.set(COOKIE_NAME, secrets.token_urlsafe(32), domain="app.test")
    client.get("/api/v1/me", headers=viewer("198.51.100.3"))

    assert flooded == 5  # the burst of unknown sessions an IP may present
    assert dynamodb.calls["get_item"] == flooded + 1  # another IP is still checked
