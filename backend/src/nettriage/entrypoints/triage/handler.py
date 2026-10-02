"""The triage Lambda's entry point (spec §3.5): `nettriage.entrypoints.triage.handler.handle`.
SQS delivers one message per invocation. The handler answers with a partial batch response,
naming the messages SQS should deliver again; it never raises, so Lambda never logs an error's
message (spec §9.3)."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any

from nettriage.entrypoints.triage.wiring import build_worker
from nettriage.entrypoints.triage.worker import TriageWorker
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)


@cache
def worker() -> TriageWorker:
    """Built on the first invocation of each Lambda instance, then reused."""
    settings = Settings()
    configure_logging(settings)
    tracer_provider = create_tracer_provider(settings)
    meter_provider = create_meter_provider(settings)
    install_global_providers(tracer_provider, meter_provider)
    return build_worker(settings, tracer_provider, meter_provider)


def handle(event: Mapping[str, Any], context: object) -> dict[str, list[dict[str, str]]]:
    current = worker()
    try:
        retry = current.handle_batch(event.get("Records", []))
    finally:
        current.flush()
    return {"batchItemFailures": [{"itemIdentifier": message_id} for message_id in retry]}
