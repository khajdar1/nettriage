"""Organization rules (spec §5.2, §5.7, §6.3): quotas, slugs, invitation tokens and emails."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from datetime import timedelta

MAX_MEMBERS_PER_ORG = 10
MAX_ORGS_PER_USER = 3
MAX_PENDING_INVITATIONS = 20
INVITATION_LIFETIME = timedelta(days=7)
MAX_SLUG = 60
MAX_EMAIL = 320
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def slugify(name: str) -> str:
    """A slug from an org's name: lowercase ASCII words joined by dashes, at most 50 characters
    (leaving room for a suffix), or "org" if nothing is left."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_name.lower())
    slug = "-".join(words)[:50].strip("-")
    return slug or "org"


def with_suffix(slug: str) -> str:
    """The slug with a random suffix, for when the plain one is taken."""
    return f"{slug[: MAX_SLUG - 7]}-{secrets.token_hex(3)}"


def new_invitation_token() -> str:
    """32 random bytes, base64url-encoded; shown once in the invitation link (spec §6.3)."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_url(app_origin: str, token: str) -> str:
    """The token goes in the fragment, which browsers never send to servers (spec §6.3)."""
    return f"{app_origin}/invite#{token}"


def normalize_email(value: str) -> str | None:
    """The address as typed, trimmed; None if it can't be an email address."""
    email = value.strip()
    if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email):
        return None
    return email
