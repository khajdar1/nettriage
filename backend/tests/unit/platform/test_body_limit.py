"""Request bodies are at most 64 KB (spec §6.7)."""

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.body_limit import MAX_BODY_BYTES
from nettriage.platform.config import Settings


def app_with_a_body_route(settings: Settings, services: Services) -> TestClient:
    app: FastAPI = create_app(settings, services)

    @app.post("/api/test-body")
    def take(payload: dict[str, Any]) -> dict[str, int]:
        return {"keys": len(payload)}

    return TestClient(app, base_url="https://app.test")


def json_of(size: int) -> bytes:
    """A JSON object exactly `size` bytes long."""
    return b'{"k":"' + b"x" * (size - 8) + b'"}'


def test_a_body_at_the_limit_is_accepted(settings: Settings, services: Services) -> None:
    client = app_with_a_body_route(settings, services)

    response = client.post(
        "/api/test-body",
        content=json_of(MAX_BODY_BYTES),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_a_declared_body_over_the_limit_is_refused_before_anything_runs(
    settings: Settings, services: Services
) -> None:
    client = app_with_a_body_route(settings, services)

    response = client.post(
        "/api/auth/logout",
        content=json_of(MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "Request bodies are limited to 64 KB."


def test_a_body_sent_in_pieces_without_a_length_is_counted(
    settings: Settings, services: Services
) -> None:
    client = app_with_a_body_route(settings, services)

    def pieces() -> Iterator[bytes]:
        body = json_of(MAX_BODY_BYTES * 2)
        for start in range(0, len(body), 8192):
            yield body[start : start + 8192]

    response = client.post(
        "/api/test-body", content=pieces(), headers={"Content-Type": "application/json"}
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
