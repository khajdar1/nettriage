from datetime import UTC, datetime, timedelta
from uuid import uuid4

from nettriage.application.sessions import Session, new_secret, session_key

START = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def session(created: datetime = START, seen: datetime = START) -> Session:
    return Session(
        key="k", user_id=uuid4(), csrf_token=new_secret(), created_at=created, last_seen_at=seen
    )


def test_a_session_expires_after_sixty_idle_minutes() -> None:
    active = session()

    assert not active.is_expired(START + timedelta(minutes=59, seconds=59))
    assert active.is_expired(START + timedelta(minutes=60))


def test_activity_can_not_keep_a_session_alive_past_twelve_hours() -> None:
    busy = session(seen=START + timedelta(hours=11, minutes=59))

    assert not busy.is_expired(START + timedelta(hours=11, minutes=59, seconds=59))
    assert busy.is_expired(START + timedelta(hours=12))


def test_activity_is_recorded_at_most_every_five_minutes() -> None:
    active = session()

    assert not active.needs_touch(START + timedelta(minutes=4, seconds=59))
    assert active.needs_touch(START + timedelta(minutes=5))


def test_secrets_are_32_random_bytes_and_only_their_hash_is_a_key() -> None:
    secret = new_secret()

    assert len(secret) == 43  # 32 bytes, base64url without padding
    assert secret != new_secret()
    assert len(session_key(secret)) == 64
    assert secret not in session_key(secret)
