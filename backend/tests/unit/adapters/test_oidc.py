import time
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from conftest import FakeClock
from fake_idp import CLIENT_ID, CLIENT_SECRET, FakeIdentityProvider, settings

from nettriage.adapters.oidc import OidcClient, OidcError, OidcUnavailableError


@pytest.fixture
def idp() -> FakeIdentityProvider:
    return FakeIdentityProvider()


@pytest.fixture
def oidc(idp: FakeIdentityProvider, clock: FakeClock) -> OidcClient:
    return OidcClient(settings(), httpx.Client(transport=idp.transport()), clock)


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def test_the_authorization_url_asks_for_a_code_with_pkce(oidc: OidcClient) -> None:
    url = oidc.authorization_url(state="s", nonce="n", code_challenge="c")

    assert url.startswith(
        "https://nettriage-test.auth.eu-north-1.amazoncognito.com/oauth2/authorize?"
    )
    assert query(url) == {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": "https://app.test/api/auth/callback",
        "scope": "openid email profile",
        "state": "s",
        "nonce": "n",
        "code_challenge": "c",
        "code_challenge_method": "S256",
        "prompt": "login",
    }


def test_the_logout_url_returns_to_the_app(oidc: OidcClient) -> None:
    url = oidc.logout_url()

    assert url.startswith("https://nettriage-test.auth.eu-north-1.amazoncognito.com/logout?")
    assert query(url) == {"client_id": CLIENT_ID, "logout_uri": "https://app.test/"}


def test_a_valid_code_gives_the_verified_identity(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    code = idp.issue_code(nonce="n-1", sub="abc", email="Ada@Example.com")

    identity = oidc.identity(code=code, code_verifier="v-1", nonce="n-1")

    assert (identity.sub, identity.email) == ("abc", "Ada@Example.com")
    [form] = idp.token_requests
    assert form["code_verifier"] == ["v-1"]
    assert form["redirect_uri"] == ["https://app.test/api/auth/callback"]


def test_the_client_secret_is_sent_as_basic_auth_not_in_the_form(
    idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    seen: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return idp.transport().handle_request(request)

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(spy)), clock)
    client.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

    token_request = next(r for r in seen if r.url.path == "/oauth2/token")
    assert token_request.headers["authorization"].startswith("Basic ")
    assert CLIENT_SECRET not in token_request.content.decode()


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"aud": "someone-else"}, "InvalidAudienceError"),
        ({"iss": "https://evil.example"}, "InvalidIssuerError"),
        ({"exp": int(time.time()) - 3600}, "ExpiredSignatureError"),
        ({"token_use": "access"}, "isn't an ID token"),
        ({"email_verified": False}, "isn't verified"),
        ({"email_verified": "false"}, "isn't verified"),
        ({"email": ""}, "no usable email"),
    ],
)
def test_a_bad_id_token_is_refused(
    oidc: OidcClient, idp: FakeIdentityProvider, claims: dict[str, Any], reason: str
) -> None:
    code = idp.issue_code(nonce="n-1", **claims)

    with pytest.raises(OidcError, match=reason):
        oidc.identity(code=code, code_verifier="v", nonce="n-1")


def test_an_id_token_for_another_sign_in_is_refused(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    code = idp.issue_code(nonce="nonce-of-another-sign-in")

    with pytest.raises(OidcError, match="nonce doesn't match"):
        oidc.identity(code=code, code_verifier="v", nonce="n-1")


def test_a_token_signed_with_an_unknown_key_is_refused(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    code = idp.issue_code(nonce="n")
    claims = idp.codes[code]
    forged = idp.sign(claims, kid="attacker-key")

    with pytest.raises(OidcError, match="signing key is unknown"):
        oidc.verify(forged, "n")


def test_an_unsigned_token_is_refused(oidc: OidcClient, idp: FakeIdentityProvider) -> None:
    claims = idp.codes[idp.issue_code(nonce="n")]
    unsigned = jwt.encode(claims, key="", algorithm="none")

    with pytest.raises(OidcError, match="RS256"):
        oidc.verify(unsigned, "n")


def test_a_rejected_code_is_an_error_without_the_code(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    idp.token_status = 400

    with pytest.raises(OidcError, match="answered 400") as caught:
        oidc.identity(code="secret-code", code_verifier="v", nonce="n")

    assert "secret-code" not in str(caught.value)


def test_an_unreachable_cognito_is_an_error(clock: FakeClock) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(down)), clock)

    with pytest.raises(OidcUnavailableError, match="couldn't be reached"):
        client.identity(code="c", code_verifier="v", nonce="n")


def test_keys_are_fetched_once_and_refetched_for_a_new_key_at_most_every_five_minutes(
    oidc: OidcClient, idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    for _ in range(3):
        oidc.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")
    rotated = idp.sign(idp.codes[idp.issue_code(nonce="n")], kid="rotated")
    for _ in range(3):
        with pytest.raises(OidcError):
            oidc.verify(rotated, "n")
    assert idp.jwks_requests == 1

    clock.advance(timedelta(minutes=5))
    with pytest.raises(OidcError):
        oidc.verify(rotated, "n")

    assert idp.jwks_requests == 2


def test_a_failed_key_fetch_is_retried_on_the_next_sign_in(
    idp: FakeIdentityProvider, clock: FakeClock
) -> None:
    """One network blip at a cold start must not block every sign-in on that Lambda instance
    for 5 minutes."""
    failures = [True]

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jwks.json") and failures:
            failures.pop()
            raise httpx.ConnectError("blip", request=request)
        return idp.transport().handle_request(request)

    client = OidcClient(settings(), httpx.Client(transport=httpx.MockTransport(flaky)), clock)
    with pytest.raises(OidcUnavailableError, match="signing keys couldn't be fetched"):
        client.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

    identity = client.identity(
        code=idp.issue_code(nonce="n", sub="abc"), code_verifier="v", nonce="n"
    )

    assert identity.sub == "abc"


def test_a_cognito_server_error_is_unavailable_but_a_refused_code_is_not(
    oidc: OidcClient, idp: FakeIdentityProvider
) -> None:
    idp.token_status = 503
    with pytest.raises(OidcUnavailableError, match="answered 503"):
        oidc.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")

    idp.token_status = 400
    with pytest.raises(OidcError, match="answered 400") as refused:
        oidc.identity(code=idp.issue_code(nonce="n"), code_verifier="v", nonce="n")
    assert not isinstance(refused.value, OidcUnavailableError)
