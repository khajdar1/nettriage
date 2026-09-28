"""Browser sessions (spec §6.2). The cookie holds 32 random bytes; the server keeps only their
SHA-256, so a leaked session table can't be replayed as cookies."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

COOKIE_NAME = "__Host-session"
IDLE_TIMEOUT = timedelta(minutes=60)
ABSOLUTE_TIMEOUT = timedelta(hours=12)
# last_seen_at is rewritten at most this often, to save DynamoDB writes (spec §5.5).
TOUCH_INTERVAL = timedelta(minutes=5)


def new_secret() -> str:
    """32 random bytes, base64url-encoded: a session ID or a CSRF token."""
    return secrets.token_urlsafe(32)


def session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    key: str
    user_id: UUID
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime

    @property
    def expires_at(self) -> datetime:
        return min(self.created_at + ABSOLUTE_TIMEOUT, self.last_seen_at + IDLE_TIMEOUT)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def needs_touch(self, now: datetime) -> bool:
        return now - self.last_seen_at >= TOUCH_INTERVAL
