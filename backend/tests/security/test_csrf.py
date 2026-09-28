"""CSRF protection on state-changing requests (spec §6.2): the session's token in X-CSRF-Token,
Sec-Fetch-Site same-origin or none, and Origin, when present, equal to the app's origin."""

from uuid import uuid4

import pytest
from browser import csrf_headers, sign_in
from conftest import counter
from fake_idp import FakeIdentityProvider
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def signed_in_client(database_client: TestClient, idp: FakeIdentityProvider) -> TestClient:
    sign_in(database_client, idp, sub=f"sub-{uuid4()}")
    return database_client


def test_the_right_token_from_the_app_itself_is_accepted(signed_in_client: TestClient) -> None:
    response = signed_in_client.post("/api/auth/logout", headers=csrf_headers(signed_in_client))

    assert response.status_code == 200


def test_a_request_the_user_typed_or_bookmarked_is_accepted(signed_in_client: TestClient) -> None:
    headers = {**csrf_headers(signed_in_client), "Sec-Fetch-Site": "none"}
    del headers["Origin"]

    assert signed_in_client.post("/api/auth/logout", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"X-CSRF-Token": None},
        {"X-CSRF-Token": "wrong"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": None},
        {"Origin": "https://evil.example"},
    ],
)
def test_a_forged_request_is_refused(
    signed_in_client: TestClient,
    metric_reader: InMemoryMetricReader,
    change: dict[str, str | None],
) -> None:
    headers = csrf_headers(signed_in_client)
    for name, value in change.items():
        if value is None:
            del headers[name]
        else:
            headers[name] = value

    response = signed_in_client.post("/api/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert signed_in_client.get("/api/v1/me").status_code == 200  # still signed in
    assert counter(metric_reader, "nettriage.csrf.failed") == 1
