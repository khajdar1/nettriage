"""Kill switches (spec §9.7): on/off flags in SSM Parameter Store that the owner can flip
without a deploy. Each is re-read at most once a minute."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from nettriage.application.clock import Clock

if TYPE_CHECKING:
    from types_boto3_ssm.client import SSMClient

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(seconds=60)


class KillSwitch:
    """On only while its parameter reads `true`. A failed read keeps the last value; until one
    read has succeeded, the switch is off, so an unreadable switch never opens anything."""

    def __init__(
        self, read: Callable[[], str], clock: Clock, refresh: timedelta = REFRESH_INTERVAL
    ) -> None:
        self._read = read
        self._clock = clock
        self._refresh = refresh
        self._on = False
        self._read_at: datetime | None = None

    def is_on(self) -> bool:
        now = self._clock()
        if self._read_at is None or now - self._read_at >= self._refresh:
            self._read_at = now
            try:
                self._on = self._read().strip().lower() == "true"
            except BotoCoreError, ClientError:
                logger.warning("kill_switch_read_failed")
        return self._on


def ssm_parameter(client: SSMClient, name: str) -> Callable[[], str]:
    """Reads the parameter's value; a parameter that doesn't exist reads as empty (off)."""

    def read() -> str:
        found = client.get_parameters(Names=[name])["Parameters"]
        return found[0]["Value"] if found else ""

    return read
