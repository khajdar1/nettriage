"""The analyze worker end to end (spec §4.2, §8.6, §9.1 to §9.3): an S3 event from the queue, the
object from S3 (moto), and the findings in Postgres as `app_analyze`."""

import gzip
import hashlib
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID

import boto3
import pytest
from conftest import NO_DATABASE, Database, FakeClock, counter
from flowlogs import port_scan
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine, text
from sqlalchemy.exc import DataError, OperationalError
from tenantdata import add_member, add_org, add_user

from nettriage.adapters.analysis_store import ClaimedUpload, claim_upload
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.analysis import UploadKey
from nettriage.application.uploads import GAVE_UP, s3_key
from nettriage.domain.parsing.vpc_flow_logs import ParseLimits
from nettriage.entrypoints.analyze import handler
from nettriage.entrypoints.analyze.worker import SIZE_MISMATCH, AnalysisFailed, Worker
from nettriage.platform.metrics import AnalyzeMetrics

BUCKET = "nettriage-test-uploads-00000000"
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@dataclass
class Rig:
    worker: Worker
    s3: object
    sqs: Any
    queue_url: str
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader


@pytest.fixture
def rig(database: Database, clock: FakeClock) -> Iterator[Rig]:
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-north-1")
        s3.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        spans = InMemorySpanExporter()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
        reader = InMemoryMetricReader()
        sqs = boto3.client("sqs", region_name="eu-north-1")
        queue_url = sqs.create_queue(QueueName="nettriage-test-triage")["QueueUrl"]
        worker = Worker(
            database=database.app_analyze,
            objects=UploadObjects(s3, BUCKET),
            triage=TriageQueue(sqs, queue_url),
            clock=clock,
            metrics=AnalyzeMetrics(MeterProvider(metric_readers=[reader])),
            tracer=tracer_provider.get_tracer("test"),
            sleep=lambda seconds: None,
        )
        yield Rig(worker=worker, s3=s3, sqs=sqs, queue_url=queue_url, spans=spans, metrics=reader)


def uploaded(
    database: Database,
    rig: Rig,
    content: bytes,
    *,
    declared_size: int | None = None,
    traceparent: str | None = None,
) -> str:
    """A pending upload whose file is in the bucket, as the browser would have PUT it."""
    with database.admin.begin() as connection:
        owner = add_user(connection)
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        upload = UUID(int=int(hashlib.sha256(content + org.bytes).hexdigest()[:32], 16))
        key = s3_key(org, upload)
        connection.execute(
            text(
                "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "
                "size_bytes, sha256) VALUES (:id, :org, :by, 'flows.log', :key, :size, :sha)"
            ),
            {
                "id": upload,
                "org": org,
                "by": owner,
                "key": key,
                "size": declared_size or len(content),
                "sha": hashlib.sha256(content).hexdigest(),
            },
        )
    metadata = {"traceparent": traceparent} if traceparent else {}
    rig.s3.put_object(Bucket=BUCKET, Key=key, Body=content, Metadata=metadata)  # type: ignore[attr-defined]
    return key


def message(
    key: str, sent_millis: int = 1_790_683_200_000, receive_count: int = 1
) -> dict[str, object]:
    """An SQS record carrying S3's ObjectCreated notification, with its URL-encoded key, on its
    `receive_count`th delivery."""
    body = {
        "Records": [{"eventName": "ObjectCreated:Put", "s3": {"object": {"key": quote_plus(key)}}}]
    }
    attributes = {"SentTimestamp": str(sent_millis), "ApproximateReceiveCount": str(receive_count)}
    return {"body": json.dumps(body), "attributes": attributes}


def upload_of(database: Database, key: str) -> tuple[str, str | None, int]:
    with database.admin.begin() as connection:
        row = connection.execute(
            text(
                "SELECT u.status, u.failure_reason, count(f.id) AS findings FROM uploads u "
                "LEFT JOIN findings f ON f.upload_id = u.id WHERE u.s3_key = :key "
                "GROUP BY u.status, u.failure_reason"
            ),
            {"key": key},
        ).one()
    return row.status, row.failure_reason, row.findings


def test_an_uploaded_port_scan_is_analyzed_and_its_finding_stored(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan())

    rig.worker.handle_message(message(key))

    assert upload_of(database, key) == ("analyzed", None, 1)
    assert counter(rig.metrics, "nettriage.uploads.processed") == 1
    assert counter(rig.metrics, "nettriage.findings.created") == 1
    assert counter(rig.metrics, "nettriage.rows.parsed") == 150


def test_the_workers_span_links_to_the_upload_request_and_times_each_step(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan(), traceparent=TRACEPARENT)

    rig.worker.handle_message(message(key))

    spans = {span.name: span for span in rig.spans.get_finished_spans()}
    upload = spans["analyze.upload"]
    [link] = upload.links
    assert format(link.context.trace_id, "032x") == TRACEPARENT.split("-")[1]
    assert upload.context is not None
    for step in ("analyze.parse", "analyze.detect"):
        parent = spans[step].parent
        assert parent is not None
        assert parent.span_id == upload.context.span_id


def test_a_file_that_is_not_a_flow_log_fails_its_upload_with_a_reason(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, b"<html><body>not flows</body></html>\n" * 20)

    rig.worker.handle_message(message(key))

    status, reason, findings = upload_of(database, key)
    assert (status, findings) == ("failed", 0)
    assert reason
    assert counter(rig.metrics, "nettriage.upload.rejected") == 1


def test_a_file_of_another_size_than_declared_fails(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, port_scan(), declared_size=len(port_scan()) + 1)

    rig.worker.handle_message(message(key))

    assert upload_of(database, key) == ("failed", SIZE_MISMATCH, 0)


def test_a_zip_bomb_fails_early_instead_of_filling_memory(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, gzip.compress(port_scan() * 1_000))
    worker = replace(rig.worker, limits=ParseLimits(max_decompressed_bytes=1_000_000))

    worker.handle_message(message(key))

    status, reason, _ = upload_of(database, key)
    assert status == "failed"
    assert reason is not None
    assert "MB" in reason or "bytes" in reason


def test_a_second_delivery_of_the_same_event_changes_nothing(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, port_scan())
    rig.worker.handle_message(message(key))

    again = rig.worker.process(key)

    assert again == "ignored"
    assert upload_of(database, key) == ("analyzed", None, 1)


def test_objects_that_are_not_uploads_and_s3s_test_event_are_ignored(rig: Rig) -> None:
    rig.worker.handle_message({"body": json.dumps({"Event": "s3:TestEvent"})})

    outcome = rig.worker.process("somewhere/else.txt")

    assert outcome == "ignored"


def test_a_brief_database_outage_is_retried(
    database: Database, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = uploaded(database, rig, port_scan())
    calls: list[str] = []

    def flaky_claim(engine: Engine, upload_key: UploadKey) -> ClaimedUpload | None:
        calls.append(upload_key.key)
        if len(calls) == 1:
            raise OperationalError("SELECT", {}, Exception("server closed the connection"))
        return claim_upload(engine, upload_key)

    monkeypatch.setattr("nettriage.entrypoints.analyze.worker.claim_upload", flaky_claim)
    slept: list[float] = []
    worker = replace(rig.worker, sleep=slept.append)

    outcome = worker.process(key)

    assert outcome == "analyzed"
    assert slept == [1.0]
    assert upload_of(database, key) == ("analyzed", None, 1)


def test_a_longer_database_outage_raises_so_sqs_delivers_the_message_again(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan())
    slept: list[float] = []
    offline = replace(rig.worker, database=create_database_engine(NO_DATABASE), sleep=slept.append)

    with pytest.raises(AnalysisFailed):
        offline.handle_message(message(key))

    assert slept == [1.0, 3.0]
    assert upload_of(database, key) == ("pending_upload", None, 0)


def test_the_logs_never_contain_a_line_of_the_file(
    database: Database, rig: Rig, logs: io.StringIO
) -> None:
    secret = b"ACCOUNT-SECRET-7f3a not a flow record\n"
    key = uploaded(database, rig, port_scan() + secret * 40)

    rig.worker.handle_message(message(key))

    assert upload_of(database, key)[0] == "failed"
    assert "ACCOUNT-SECRET-7f3a" not in logs.getvalue()


def test_the_handler_flushes_telemetry_even_when_a_message_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flushed: list[bool] = []

    class Failing:
        def handle_message(self, record: object) -> None:
            raise RuntimeError("boom")

        def flush(self) -> None:
            flushed.append(True)

    monkeypatch.setattr(handler, "worker", lambda: Failing())

    with pytest.raises(RuntimeError):
        handler.handle({"Records": [{"body": "{}"}]}, None)

    assert flushed == [True]


def test_an_unexpected_error_is_raised_and_logged_without_the_files_content(
    database: Database, rig: Rig, logs: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database error quotes the values it was given, such as a rejected line's sample
    (spec §9.3). Lambda logs whatever the handler raises, and spans go to Grafana."""
    private_line = "ACCOUNT-SECRET-7f3a not a flow record"
    key = uploaded(database, rig, port_scan() + f"{private_line}\n".encode())

    def failing_store(*args: object) -> bool:
        quoted = Exception(f'bad value "{private_line}"')
        raise DataError("UPDATE uploads", {"samples": private_line}, quoted)

    monkeypatch.setattr("nettriage.entrypoints.analyze.worker.store_analysis", failing_store)

    with pytest.raises(AnalysisFailed) as raised:
        rig.worker.handle_message(message(key))

    assert str(raised.value) == "DataError"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert "analyze_failed" in logs.getvalue()
    assert private_line not in logs.getvalue()
    for span in rig.spans.get_finished_spans():
        assert private_line not in str(span.status.description)
        assert all(private_line not in str(event.attributes) for event in span.events)


def test_the_last_delivery_fails_an_upload_the_worker_cant_finish(
    database: Database, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = uploaded(database, rig, port_scan())

    def failing_store(*args: object) -> bool:
        raise RuntimeError("a bug")

    monkeypatch.setattr("nettriage.entrypoints.analyze.worker.store_analysis", failing_store)

    with pytest.raises(AnalysisFailed):
        rig.worker.handle_message(message(key, receive_count=3))

    assert upload_of(database, key) == ("failed", GAVE_UP, 0)
    assert counter(rig.metrics, "nettriage.upload.rejected") == 1


def test_an_earlier_delivery_leaves_the_upload_for_sqs_to_try_again(
    database: Database, rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = uploaded(database, rig, port_scan())

    def failing_store(*args: object) -> bool:
        raise RuntimeError("a bug")

    monkeypatch.setattr("nettriage.entrypoints.analyze.worker.store_analysis", failing_store)

    with pytest.raises(AnalysisFailed):
        rig.worker.handle_message(message(key, receive_count=2))

    assert upload_of(database, key) == ("processing", None, 0)


def queued(rig: Rig) -> list[dict[str, Any]]:
    """The triage messages waiting in the queue, taken off it."""
    messages: list[dict[str, Any]] = []
    while batch := rig.sqs.receive_message(
        QueueUrl=rig.queue_url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
    ).get("Messages"):
        messages += batch
        for found in batch:
            rig.sqs.delete_message(QueueUrl=rig.queue_url, ReceiptHandle=found["ReceiptHandle"])
    return messages


def finding_ids(database: Database, key: str) -> list[str]:
    with database.admin.begin() as connection:
        found: list[UUID] = list(
            connection.execute(
                text(
                    "SELECT f.id FROM findings f JOIN uploads u ON u.id = f.upload_id "
                    "WHERE u.s3_key = :key"
                ),
                {"key": key},
            ).scalars()
        )
    return [str(finding) for finding in found]


def test_an_analyzed_uploads_findings_are_queued_for_ai_with_the_workers_trace(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan(), traceparent=TRACEPARENT)

    rig.worker.handle_message(message(key))

    [sent] = queued(rig)
    assert json.loads(sent["Body"])["finding_id"] == finding_ids(database, key)[0]
    upload_span = next(s for s in rig.spans.get_finished_spans() if s.name == "analyze.upload")
    traceparent = sent["MessageAttributes"]["traceparent"]["StringValue"]
    assert traceparent.split("-")[1] == format(upload_span.context.trace_id, "032x")


def test_a_second_delivery_queues_the_findings_again(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, port_scan())
    rig.worker.handle_message(message(key))

    again = rig.worker.process(key)

    assert again == "ignored"
    bodies = [json.loads(sent["Body"])["finding_id"] for sent in queued(rig)]
    assert bodies == finding_ids(database, key) * 2


def test_a_failed_send_fails_the_message_and_the_next_delivery_queues(
    database: Database, rig: Rig
) -> None:
    key = uploaded(database, rig, port_scan())
    unreachable = TriageQueue(rig.sqs, rig.queue_url.replace("triage", "missing"))
    broken = replace(rig.worker, triage=unreachable)

    with pytest.raises(AnalysisFailed):
        broken.handle_message(message(key))
    rig.worker.handle_message(message(key, receive_count=2))

    assert upload_of(database, key) == ("analyzed", None, 1)
    assert len(queued(rig)) == 1


def test_a_file_that_fails_queues_nothing(database: Database, rig: Rig) -> None:
    key = uploaded(database, rig, b"this is not a flow log\n")

    rig.worker.handle_message(message(key))

    assert queued(rig) == []
