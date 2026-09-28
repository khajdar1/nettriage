"""The sign-in flow's rules (spec §4.1): where the user may return to, and PKCE."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

DEFAULT_RETURN_TO = "/app"
MAX_RETURN_TO = 512


@dataclass(frozen=True)
class LoginState:
    """What the callback needs from the login that started it; kept for 5 minutes."""

    code_verifier: str
    nonce: str
    return_to: str


def safe_return_to(value: str | None) -> str:
    """A path inside this app, or /app. It must start with a single `/`, so it has no scheme and
    no host; anything a browser could still read as another origin is refused too: `//host`,
    backslashes (browsers read `/\\host` as `//host`) and control characters. This is the
    open-redirect protection."""
    if not value or len(value) > MAX_RETURN_TO or not value.startswith("/"):
        return DEFAULT_RETURN_TO
    if value.startswith("//") or "\\" in value:
        return DEFAULT_RETURN_TO
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return DEFAULT_RETURN_TO
    return value


def new_code_verifier() -> str:
    """64 random bytes, base64url-encoded: 86 characters, inside RFC 7636's 43 to 128."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """RFC 7636's S256 method."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
