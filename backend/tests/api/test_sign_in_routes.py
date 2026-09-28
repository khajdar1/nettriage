"""Sign-in through Cognito (spec §4.1, §6.2, §6.3)."""

from uuid import uuid4

from browser import finish_sign_in, query, set_cookie, sign_in, start_sign_in
from conftest import Database, RuntimeTable, counter
from fake_idp import DOMAIN, FakeIdentityProvider
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import text

from nettriage.application.sessions import COOKIE_NAME
from nettriage.application.sign_in import code_challenge


def audit_rows(database: Database, action: str, email: str) -> list[tuple[str, dict[str, object]]]:
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT a.outcome, a.details FROM audit_log a JOIN users u "
                "ON u.id = a.actor_user_id WHERE a.action = :action AND u.email = :email "
                "ORDER BY a.created_at"
            ),
            {"action": action, "email": email},
        ).all()
    return [(row.outcome, row.details) for row in rows]


def test_login_sends_the_browser_to_cognito_with_pkce(
    client: TestClient, runtime_table: RuntimeTable
) -> None:
    response = client.get(
        "/api/auth/login", params={"return_to": "/app/orgs"}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    location = response.headers["location"]
    assert location.startswith(f"{DOMAIN}/oauth2/authorize?")
    login = query(location)
    stored = runtime_table.client.get_item(
        TableName=runtime_table.name, Key={"pk": {"S": f"LOGIN#{login['state']}"}}
    )["Item"]
    assert stored["return_to"]["S"] == "/app/orgs"
    assert code_challenge(stored["code_verifier"]["S"]) == login["code_challenge"]
    assert stored["nonce"]["S"] == login["nonce"]


def test_login_ignores_a_return_address_outside_the_app(
    client: TestClient, runtime_table: RuntimeTable
) -> None:
    login = start_sign_in(client, return_to="https://evil.example/")

    stored = runtime_table.client.get_item(
        TableName=runtime_table.name, Key={"pk": {"S": f"LOGIN#{login['state']}"}}
    )["Item"]
    assert stored["return_to"]["S"] == "/app"


def test_signing_in_creates_the_user_and_sets_the_session_cookie(
    database_client: TestClient,
    idp: FakeIdentityProvider,
    database: Database,
    metric_reader: InMemoryMetricReader,
) -> None:
    email = f"{uuid4().hex}@example.com"
    login = start_sign_in(database_client, return_to="/app/orgs")

    response = finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}", email=email)

    assert response.status_code == 302
    assert response.headers["location"] == "/app/orgs"
    cookie = set_cookie(response, COOKIE_NAME)
    assert cookie is not None
    for attribute in ("Max-Age=43200", "Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attribute in cookie
    assert "Domain" not in cookie
    assert database_client.get("/api/v1/me").json()["user"]["email"] == email
    assert audit_rows(database, "auth.session_created", email) == [("success", {"new_user": True})]
    assert counter(metric_reader, "nettriage.signups") == 1


def test_signing_in_again_finds_the_same_user(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    sub, email = f"sub-{uuid4()}", f"{uuid4().hex}@example.com"
    sign_in(database_client, idp, sub=sub, email=email)
    first = database_client.get("/api/v1/me").json()["user"]["id"]

    sign_in(database_client, idp, sub=sub, email=email)

    assert database_client.get("/api/v1/me").json()["user"]["id"] == first
    assert [details for _, details in audit_rows(database, "auth.session_created", email)] == [
        {"new_user": True},
        {"new_user": False},
    ]


def test_a_sign_in_state_works_only_once(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = start_sign_in(database_client)
    finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}")

    replayed = finish_sign_in(database_client, idp, login, sub=f"sub-{uuid4()}")

    assert replayed.headers["location"] == "/?sign_in=expired"
    assert set_cookie(replayed, COOKIE_NAME) is None


def test_an_unknown_state_is_refused(client: TestClient) -> None:
    response = client.get(
        "/api/auth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False
    )

    assert response.headers["location"] == "/?sign_in=expired"
    assert set_cookie(response, COOKIE_NAME) is None


def test_a_cancelled_sign_in_is_reported(client: TestClient) -> None:
    login = start_sign_in(client)

    response = client.get(
        "/api/auth/callback",
        params={"error": "access_denied", "state": login["state"]},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?sign_in=failed"


def test_an_id_token_for_another_sign_in_is_refused(
    client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = start_sign_in(client)
    code = idp.issue_code(nonce="nonce-from-another-sign-in")

    response = client.get(
        "/api/auth/callback",
        params={"code": code, "state": login["state"]},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?sign_in=failed"
    assert set_cookie(response, COOKIE_NAME) is None


def test_a_disabled_account_cannot_sign_in(
    database_client: TestClient, idp: FakeIdentityProvider, database: Database
) -> None:
    sub, email = f"sub-{uuid4()}", f"{uuid4().hex}@example.com"
    sign_in(database_client, idp, sub=sub, email=email)
    with database.admin.begin() as connection:
        connection.execute(
            text("UPDATE users SET disabled_at = now() WHERE cognito_sub = :sub"), {"sub": sub}
        )
    database_client.cookies.clear()

    response = sign_in(database_client, idp, sub=sub, email=email)

    assert response.headers["location"] == "/?sign_in=disabled"
    assert set_cookie(response, COOKIE_NAME) is None
    assert audit_rows(database, "auth.session_created", email)[-1] == ("denied", {})


def test_every_sign_in_gets_a_new_session_and_ends_the_one_the_browser_had(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    """Session fixation: a session ID the browser held before signing in never survives it."""
    sub = f"sub-{uuid4()}"
    sign_in(database_client, idp, sub=sub)
    before = database_client.cookies["__Host-session"]

    sign_in(database_client, idp, sub=sub)
    after = database_client.cookies["__Host-session"]

    assert after != before
    database_client.cookies.set("__Host-session", before, domain="app.test")
    assert database_client.get("/api/v1/me").status_code == 401


def test_a_sign_in_finished_in_another_browser_is_refused(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    """Login CSRF: an attacker starts a sign-in, then sends the victim the callback link with the
    attacker's code. The victim's browser didn't start that sign-in, so it must not finish it."""
    attacker_login = start_sign_in(database_client)
    code = idp.issue_code(nonce=attacker_login["nonce"], sub=f"sub-{uuid4()}")
    victim = TestClient(database_client.app, base_url="https://app.test")

    response = victim.get(
        "/api/auth/callback",
        params={"code": code, "state": attacker_login["state"]},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?sign_in=expired"
    assert COOKIE_NAME not in victim.cookies


def test_the_sign_in_cookie_lives_five_minutes_and_the_callback_clears_it(
    database_client: TestClient, idp: FakeIdentityProvider
) -> None:
    login = database_client.get("/api/auth/login", follow_redirects=False)
    cookie = set_cookie(login, "__Host-sign-in")
    assert cookie is not None
    for attribute in ("Max-Age=300", "Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert attribute in cookie

    callback = finish_sign_in(
        database_client, idp, query(login.headers["location"]), sub=f"sub-{uuid4()}"
    )

    assert set_cookie(callback, "__Host-sign-in") == (
        "__Host-sign-in=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
    )
