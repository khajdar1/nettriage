"""A fake Cognito for tests: its token endpoint and published keys, served through httpx's mock
transport. It signs real RS256 ID tokens, so the verifier under test is the production one."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import cache
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from nettriage.adapters.oidc import OidcSettings

ISSUER = "https://cognito-idp.eu-north-1.amazonaws.com/eu-north-1_Test"
DOMAIN = "https://nettriage-test.auth.eu-north-1.amazoncognito.com"
CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"  # noqa: S105 - the fake provider's own secret
APP_ORIGIN = "https://app.test"
KEY_ID = "test-key"


@cache
def signing_key() -> rsa.RSAPrivateKey:
    """One RSA key for the whole test run: generating one takes a noticeable fraction of a
    second."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings() -> OidcSettings:
    return OidcSettings(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        domain=DOMAIN,
        app_origin=APP_ORIGIN,
    )


@dataclass
class FakeIdentityProvider:
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_requests: list[dict[str, list[str]]] = field(default_factory=list)
    jwks_requests: int = 0
    token_status: int = 200
    issued: int = 0

    def issue_code(
        self, *, nonce: str, sub: str = "user-sub", email: str = "u@example.com", **claims: Any
    ) -> str:
        """A code the token endpoint will exchange for an ID token with these claims."""
        self.issued += 1
        code = f"code-{self.issued}"
        now = int(time.time())
        self.codes[code] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": sub,
            "email": email,
            "email_verified": True,
            "token_use": "id",
            "nonce": nonce,
            "iat": now,
            "exp": now + 3600,
            **claims,
        }
        return code

    def sign(self, claims: dict[str, Any], *, kid: str = KEY_ID) -> str:
        return jwt.encode(claims, signing_key(), algorithm="RS256", headers={"kid": kid})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/jwks.json":
            self.jwks_requests += 1
            public = json.loads(RSAAlgorithm.to_jwk(signing_key().public_key()))
            return httpx.Response(
                200, json={"keys": [{**public, "kid": KEY_ID, "use": "sig", "alg": "RS256"}]}
            )
        if url == f"{DOMAIN}/oauth2/token" and request.method == "POST":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            claims = self.codes.pop(form.get("code", [""])[0], None)
            if claims is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "id_token": self.sign(claims),
                    "access_token": "a",
                    "refresh_token": "r",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404)
