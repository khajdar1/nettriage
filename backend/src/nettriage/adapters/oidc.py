"""Cognito as the OpenID Connect provider (spec §4.1, §6.1).

The backend runs the authorization-code flow with PKCE, exchanges the code with the client
secret, and verifies the ID token against Cognito's published keys: signature (RS256), issuer,
audience, expiry, nonce, `token_use` and `email_verified`. Cognito's tokens are then discarded.
Error messages never include a token or a code.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from nettriage.application.clock import Clock

SCOPES = "openid email profile"
JWKS_REFRESH_INTERVAL = timedelta(minutes=5)
CLOCK_LEEWAY = timedelta(seconds=30)
MAX_EMAIL = 320


@dataclass(frozen=True)
class OidcSettings:
    issuer: str  # https://cognito-idp.<region>.amazonaws.com/<user pool id>
    client_id: str
    client_secret: str
    domain: str  # https://<prefix>.auth.<region>.amazoncognito.com
    app_origin: str  # https://<cloudfront domain>

    @property
    def redirect_uri(self) -> str:
        return f"{self.app_origin}/api/auth/callback"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str


class OidcError(Exception):
    """Sign-in couldn't be completed. The message says why, without any token."""


class OidcClient:
    def __init__(self, settings: OidcSettings, http: httpx.Client, clock: Clock) -> None:
        self.settings = settings
        self._http = http
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: datetime | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.settings.domain}/oauth2/authorize?{query}"

    def logout_url(self) -> str:
        query = urlencode(
            {"client_id": self.settings.client_id, "logout_uri": f"{self.settings.app_origin}/"}
        )
        return f"{self.settings.domain}/logout?{query}"

    def identity(self, *, code: str, code_verifier: str, nonce: str) -> Identity:
        """Exchange the code and verify the ID token it returns."""
        return self.verify(self._exchange(code, code_verifier), nonce)

    def _exchange(self, code: str, code_verifier: str) -> str:
        try:
            response = self._http.post(
                f"{self.settings.domain}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.settings.client_id,
                    "code": code,
                    "redirect_uri": self.settings.redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(self.settings.client_id, self.settings.client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError:
            raise OidcError("the token endpoint couldn't be reached") from None
        if response.status_code != httpx.codes.OK:
            raise OidcError(f"the token endpoint answered {response.status_code}")
        try:
            id_token = response.json()["id_token"]
        except ValueError, KeyError, TypeError:
            raise OidcError("the token response had no ID token") from None
        if not isinstance(id_token, str):
            raise OidcError("the token response had no ID token")
        return id_token

    def verify(self, token: str, nonce: str) -> Identity:
        """Check an ID token's signature and claims, and return who it identifies."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise OidcError("the ID token is malformed") from None
        if header.get("alg") != "RS256":
            raise OidcError("the ID token isn't signed with RS256")
        key = self._key(str(header.get("kid", "")))
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.settings.client_id,
                issuer=self.settings.issuer,
                leeway=CLOCK_LEEWAY,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use", "nonce"]},
            )
        except jwt.PyJWTError as error:
            raise OidcError(f"the ID token was rejected ({type(error).__name__})") from None
        if claims["token_use"] != "id":  # noqa: S105 - a claim value, not a password
            raise OidcError("the token isn't an ID token")
        if not hmac.compare_digest(str(claims["nonce"]), nonce):
            raise OidcError("the ID token's nonce doesn't match")
        if claims.get("email_verified") not in (True, "true"):
            raise OidcError("the email address isn't verified")
        email = claims.get("email")
        if not isinstance(email, str) or not email or len(email) > MAX_EMAIL:
            raise OidcError("the ID token has no usable email address")
        return Identity(sub=str(claims["sub"]), email=email)

    def _key(self, kid: str) -> jwt.PyJWK:
        if kid not in self._keys and self._may_refresh():
            self._refresh()
        if kid not in self._keys:
            raise OidcError("the ID token's signing key is unknown")
        return self._keys[kid]

    def _may_refresh(self) -> bool:
        """Fetch the keys on first use, and again for an unknown key at most every 5 minutes,
        so tokens with made-up key IDs can't make us hammer Cognito."""
        return self._fetched_at is None or self._clock() - self._fetched_at >= JWKS_REFRESH_INTERVAL

    def _refresh(self) -> None:
        self._fetched_at = self._clock()
        try:
            response = self._http.get(self.settings.jwks_url)
            response.raise_for_status()
            keys = response.json()["keys"]
            self._keys = {
                key["kid"]: jwt.PyJWK(key)
                for key in keys
                if key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
            }
        except httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError:
            raise OidcError("Cognito's signing keys couldn't be fetched") from None
