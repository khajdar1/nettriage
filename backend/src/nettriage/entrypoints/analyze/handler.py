"""The analyze Lambda's entry point (spec §3.5): `nettriage.entrypoints.analyze.handler.handle`.
SQS delivers one message per invocation (batch size 1); an exception fails the invocation, so
SQS delivers the message again."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

from nettriage.entrypoints.analyze.wiring import build_worker
from nettriage.entrypoints.analyze.worker import Worker
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)


@cache
def worker() -> Worker:
    """Built on the first invocation of each Lambda instance, then reused."""
    settings = Settings()
    configure_logging(settings)
    tracer_provider = create_tracer_provider(settings)
    meter_provider = create_meter_provider(settings)
    install_global_providers(tracer_provider, meter_provider)
    return build_worker(settings, tracer_provider, meter_provider)


def handle(event: Mapping[str, Any], context: object) -> None:
    current = worker()
    try:
        for record in event.get("Records", []):
            current.handle_message(record)
    finally:
        current.flush()
