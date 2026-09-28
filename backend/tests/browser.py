"""Drive the sign-in flow the way a browser does: the API's login redirect, the fake Cognito's
code, and the callback. The test client keeps the session cookie like a browser would."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx2
from fake_idp import APP_ORIGIN, FakeIdentityProvider
from fastapi.testclient import TestClient

VIEWER = {"CloudFront-Viewer-Address": "203.0.113.7:4444"}


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def start_sign_in(
    client: TestClient, return_to: str | None = None, headers: dict[str, str] | None = None
) -> dict[str, str]:
    """GET /api/auth/login; returns the Cognito authorize URL's query (state, nonce, ...)."""
    params = {"return_to": return_to} if return_to else {}
    response = client.get("/api/auth/login", params=params, headers=headers, follow_redirects=False)
    assert response.status_code == 302, response.text
    return query(response.headers["location"])


def finish_sign_in(
    client: TestClient,
    idp: FakeIdentityProvider,
    login: dict[str, str],
    *,
    sub: str = "user-sub",
    email: str = "u@example.com",
) -> httpx2.Response:
    code = idp.issue_code(nonce=login["nonce"], sub=sub, email=email)
    return client.get(
        "/api/auth/callback",
        params={"code": code, "state": login["state"]},
        follow_redirects=False,
    )


def sign_in(
    client: TestClient,
    idp: FakeIdentityProvider,
    *,
    sub: str = "user-sub",
    email: str = "u@example.com",
) -> httpx2.Response:
    return finish_sign_in(client, idp, start_sign_in(client), sub=sub, email=email)


def csrf_headers(client: TestClient) -> dict[str, str]:
    """What the SPA sends on a state-changing request (spec Â§6.2, Â§7)."""
    token = client.get("/api/v1/me").json()["csrf_token"]
    return {"X-CSRF-Token": token, "Sec-Fetch-Site": "same-origin", "Origin": APP_ORIGIN}


def set_cookie(response: httpx2.Response, name: str) -> str | None:
    """The response's Set-Cookie header for `name`, if any."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return None
