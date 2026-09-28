"""The current time, injected so tests can control it."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)
