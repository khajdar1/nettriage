"""The ops Lambda's entry point (Plan 7a §2): `nettriage.entrypoints.ops.handler.handle`.
EventBridge Scheduler invokes it with `{"job": "<name>"}`, and the owner's
`just restore-drill-<stage>` with `{"job": "restore_drill"}`. It answers with the job's counts;
an exception fails the invocation, so Lambda retries an asynchronous one."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from typing import Any, Protocol

from nettriage.entrypoints.ops.wiring import build_ops
from nettriage.platform.config import Settings
from nettriage.platform.logging import configure_logging
from nettriage.platform.telemetry import (
    create_meter_provider,
    create_tracer_provider,
    install_global_providers,
)


class Runner(Protocol):
    def run(self, job: str) -> dict[str, str | int | bool]: ...

    def flush(self) -> None: ...


@cache
def ops() -> Runner:
    """Built on the first invocation of each Lambda instance, then reused."""
    settings = Settings()
    configure_logging(settings)
    tracer_provider = create_tracer_provider(settings)
    meter_provider = create_meter_provider(settings)
    install_global_providers(tracer_provider, meter_provider)
    return build_ops(settings, tracer_provider, meter_provider)


def handle(event: Mapping[str, Any], context: object) -> dict[str, str | int | bool]:
    job = event.get("job")
    if not isinstance(job, str):
        raise ValueError("the event names no job")
    current = ops()
    try:
        return current.run(job)
    finally:
        current.flush()
