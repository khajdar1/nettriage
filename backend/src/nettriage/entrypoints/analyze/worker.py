"""The analyze worker (spec §4.2, §8.6). Each SQS message carries an S3 event for one uploaded
file: claim its upload, parse, detect and store the findings. A file that isn't a flow log, or
breaks a limit, fails its upload with a readable reason. A database outage gets two quick
retries; anything else unexpected raises, so SQS retries the message (3 times, then the
dead-letter queue); storing again changes nothing.

Logs never contain a line of the file (spec §9.3): failures are logged by their code."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Literal
from urllib.parse import unquote_plus

from opentelemetry.trace import Tracer
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from nettriage.adapters.analysis_store import (
    ClaimedUpload,
    claim_upload,
    fail_upload,
    store_analysis,
)
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import analyze_parsed, parse_upload_key
from nettriage.application.clock import Clock
from nettriage.domain.parsing.vpc_flow_logs import FlowLogError, ParseLimits, parse_flow_log
from nettriage.platform.metrics import AnalyzeMetrics
from nettriage.platform.trace_context import links_from

logger = logging.getLogger(__name__)

type Outcome = Literal["analyzed", "failed", "ignored", "duplicate"]

SIZE_MISMATCH = "The file's size doesn't match the size declared when it was uploaded."
# Neon waking up or restarting takes seconds, and SQS would deliver the message again only after
# its 30-minute visibility timeout, so the worker first retries after these pauses (spec §8.6).
RETRY_DELAYS = (1.0, 3.0)


@dataclass
class Worker:
    database: Engine
    objects: UploadObjects
    clock: Clock
    metrics: AnalyzeMetrics
    tracer: Tracer
    flush: Callable[[], None] = lambda: None
    limits: ParseLimits = field(default_factory=ParseLimits)
    sleep: Callable[[float], None] = time.sleep

    def handle_message(self, record: Mapping[str, Any]) -> None:
        """One SQS record: an S3 notification, or S3's test event when the notification is set
        up."""
        self._record_age(record)
        body = json.loads(record["body"])
        if body.get("Event") == "s3:TestEvent":
            return
        for event in body.get("Records", []):
            if str(event.get("eventName", "")).startswith("ObjectCreated:"):
                # S3 notifications URL-encode keys, with `+` for spaces.
                self.process(unquote_plus(event["s3"]["object"]["key"]))

    def process(self, key: str) -> Outcome:
        outcome = self._with_retries(key)
        self.metrics.uploads_processed.add(1, {"outcome": outcome})
        return outcome

    def _with_retries(self, key: str) -> Outcome:
        for delay in RETRY_DELAYS:
            try:
                return self._process(key)
            except OperationalError:
                logger.warning("database_unavailable", extra={"retry_in_s": delay})
                self.sleep(delay)
        return self._process(key)

    def _process(self, key: str) -> Outcome:
        upload_key = parse_upload_key(key)
        if upload_key is None:
            logger.warning("upload_object_ignored", extra={"reason": "unknown_key"})
            return "ignored"
        claimed = claim_upload(self.database, upload_key)
        if claimed is None:
            logger.info("upload_object_ignored", extra={"reason": "not_pending"})
            return "ignored"
        upload = self.objects.open(key)
        started = time.monotonic()
        try:
            with self.tracer.start_as_current_span(
                "analyze.upload", links=links_from(upload.traceparent)
            ):
                return self._analyze(claimed, upload.size, upload.body)
        finally:
            upload.body.close()
            self.metrics.processing_duration.record(time.monotonic() - started)

    def _analyze(self, claimed: ClaimedUpload, size: int, body: BinaryIO) -> Outcome:
        if size != claimed.size_bytes:
            return self._fail(claimed, "size_mismatch", SIZE_MISMATCH)
        try:
            with self.tracer.start_as_current_span("analyze.parse"):
                parsed = parse_flow_log(body, self.limits)
        except FlowLogError as error:
            return self._fail(claimed, error.code, error.detail)
        with self.tracer.start_as_current_span("analyze.detect"):
            analysis = analyze_parsed(parsed)
        if not store_analysis(self.database, claimed, analysis, self.clock()):
            logger.info("upload_already_stored")
            return "duplicate"
        self.metrics.rows_parsed.add(analysis.rows_parsed)
        self.metrics.rows_rejected.add(analysis.rows_rejected)
        for finding in analysis.findings:
            self.metrics.findings_created.add(
                1, {"detector": finding.detector_id, "severity": finding.severity.value}
            )
        logger.info(
            "upload_analyzed",
            extra={"findings": len(analysis.findings), "rows_parsed": analysis.rows_parsed},
        )
        return "analyzed"

    def _fail(self, claimed: ClaimedUpload, code: str, reason: str) -> Outcome:
        fail_upload(self.database, claimed, reason, self.clock())
        self.metrics.upload_rejected.add(1, {"reason": code})
        logger.info("upload_failed", extra={"error_code": code})
        return "failed"

    def _record_age(self, record: Mapping[str, Any]) -> None:
        sent = record.get("attributes", {}).get("SentTimestamp")
        if sent is not None:
            age = self.clock().timestamp() - int(sent) / 1000
            self.metrics.queue_message_age.record(max(age, 0.0), {"queue": "analyze"})
