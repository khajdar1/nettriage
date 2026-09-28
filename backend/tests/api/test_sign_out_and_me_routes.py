"""The signed-in user, and signing out (spec §6.2, §7)."""

from uuid import uuid4

from browser import csrf_headers, sign_in
from conftest import Database
from fake_idp import CLIENT_ID, DOMAIN, FakeIdentityProvider
from fastapi.testclient import TestClient
from sqlalchemy import text
from tenantdata import add_member, add_org

from nettriage.application.sessions import COOKIE_NAME


def signed_in(client: TestClient, idp: FakeIdentityProvider) -> str:
    """Sign in a new user; returns their email."""
    email = f"{uuid4().hex}@example.com"
    sign_in(client, idp, sub=f"sub-{uuid4()}", email=email)
    return email


def test_me_shows_the_user_their_memberships_and_the_csrf_token(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    email = signed_in(database_client, idp)
    user_id = database_client.get("/api/v1/me").json()["user"]["id"]
    with database.admin.begin() as connection:
        org = add_org(connection, user_id)
        add_member(connection, org, user_id, "admin")

    response = database_client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["user"] == {"id": user_id, "email": email, "display_name": None}
    assert body["memberships"] == [
        {"org_id": str(org), "name": "Org", "slug": f"org-{org.hex}", "role": "admin"}
    ]
    assert len(body["csrf_token"]) == 43


def test_me_without_a_session_is_401_problem_details(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


def test_an_unknown_session_cookie_is_401_and_cleared(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "made-up", domain="app.test")

    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}=; Max-Age=0")


def test_signing_out_ends_the_session_and_returns_cognitos_logout_url(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    email = signed_in(database_client, idp)
    headers = csrf_headers(database_client)

    response = database_client.post("/api/auth/logout", headers=headers)

    assert response.status_code == 200
    assert response.json()["logout_url"].startswith(f"{DOMAIN}/logout?client_id={CLIENT_ID}")
    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}=; Max-Age=0")
    assert database_client.get("/api/v1/me").status_code == 401
    with database.admin.begin() as connection:
        actions: list[str] = list(
            connection.execute(
                text(
                    "SELECT a.action FROM audit_log a JOIN users u ON u.id = a.actor_user_id "
                    "WHERE u.email = :email ORDER BY a.created_at"
                ),
                {"email": email},
            ).scalars()
        )
        assert actions == ["auth.session_created", "auth.logout"]


def test_signing_out_everywhere_ends_every_session_of_the_user(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    sub = f"sub-{uuid4()}"
    sign_in(database_client, idp, sub=sub)
    laptop = database_client.cookies[COOKIE_NAME]
    database_client.cookies.clear()
    sign_in(database_client, idp, sub=sub)  # a second browser

    response = database_client.post("/api/auth/logout-all", headers=csrf_headers(database_client))

    assert response.status_code == 200
    database_client.cookies.set(COOKIE_NAME, laptop, domain="app.test")
    assert database_client.get("/api/v1/me").status_code == 401
    with database.admin.begin() as connection:
        details: dict[str, object] = connection.execute(
            text(
                "SELECT a.details FROM audit_log a JOIN users u ON u.id = a.actor_user_id "
                "WHERE u.cognito_sub = :sub AND a.action = 'auth.logout_all'"
            ),
            {"sub": sub},
        ).scalar_one()
    assert details == {"sessions": 2}
