"""The triage worker (spec §4.2, §8.6). Each SQS message names one finding to explain,
`{"org_id": …, "finding_id": …}`, with the upload's W3C traceparent as a message attribute.

A message that fails is handed back to SQS (a partial batch response): SQS delivers it again
after the visibility timeout, at most 3 times, then moves it to the dead-letter queue. A passing
Bedrock failure is handed back the same way until the last delivery, which stores `failed`.
A malformed message, or one whose finding is gone (its org was deleted), is let go.

Nothing the worker raises reaches Lambda, so failures are logged and traced by their type or
code only, never by an error's message, which can quote the finding (spec §9.3)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from opentelemetry.trace import Status, StatusCode, Tracer

from nettriage.application.clock import Clock
from nettriage.application.organizations import NotFound
from nettriage.entrypoints.triage.explainer import Explained, ExplainLater
from nettriage.platform.metrics import AiMetrics
from nettriage.platform.trace_context import links_from

logger = logging.getLogger(__name__)

# The triage queue's maxReceiveCount (infra/modules/pipeline/triage.tf).
LAST_DELIVERY = 3


class Explains(Protocol):
    def explain(
        self, org_id: UUID, finding_id: UUID, *, last_delivery: bool = True
    ) -> Explained: ...


@dataclass
class TriageWorker:
    explainer: Explains
    clock: Clock
    metrics: AiMetrics
    tracer: Tracer
    flush: Callable[[], None] = lambda: None

    def handle_batch(self, records: Iterable[Mapping[str, Any]]) -> list[str]:
        """The IDs of the messages SQS should deliver again."""
        return [record["messageId"] for record in records if not self.handle_message(record)]

    def handle_message(self, record: Mapping[str, Any]) -> bool:
        """True when the message is done with: explained, or not worth another delivery."""
        self._record_age(record)
        target = _target(record.get("body", ""))
        if target is None:
            logger.warning("triage_message_ignored", extra={"reason": "malformed"})
            return True
        deliveries = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        traceparent = record.get("messageAttributes", {}).get("traceparent", {}).get("stringValue")
        with self.tracer.start_as_current_span(
            "triage.explain",
            links=links_from(traceparent),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                explained = self.explainer.explain(
                    *target, last_delivery=deliveries >= LAST_DELIVERY
                )
            except NotFound:
                logger.info("triage_message_ignored", extra={"reason": "finding_gone"})
                return True
            except ExplainLater as later:
                span.set_status(Status(StatusCode.ERROR, later.code))
                return False
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                logger.error("triage_failed", extra={"error_code": type(error).__name__})
                return False
            span.set_attribute("nettriage.ai.outcome", explained.outcome)
        return True

    def _record_age(self, record: Mapping[str, Any]) -> None:
        sent = record.get("attributes", {}).get("SentTimestamp")
        if sent is not None:
            age = self.clock().timestamp() - int(sent) / 1000
            self.metrics.queue_message_age.record(max(age, 0.0), {"queue": "triage"})


def _target(body: str) -> tuple[UUID, UUID] | None:
    try:
        parsed = json.loads(body)
        return UUID(parsed["org_id"]), UUID(parsed["finding_id"])
    except ValueError, TypeError, KeyError, AttributeError:
        return None
