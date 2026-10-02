"""The triage worker (spec §4.2, §8.6): one SQS message per finding, explained with the real
explainer (Postgres as `app_triage`, budgets in moto, a scripted model). A message that fails is
handed back to SQS, and nothing the worker raises reaches Lambda."""

import io
import json
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid7

import pytest
from aidata import good_output
from conftest import Database, FakeClock, RuntimeTable
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from tenantdata import Tenant, add_tenant

from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.fake_llm import FakeProvider
from nettriage.application.llm import ProviderError
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.entrypoints.triage.worker import TriageWorker
from nettriage.platform.metrics import AiMetrics

ANSWER = good_output(summary="203.0.113.9 probed port 22 on 10.0.0.5, and it was rejected.")
UPLOAD_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@dataclass
class Rig:
    worker: TriageWorker
    model: FakeProvider
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader


@pytest.fixture
def rig(database: Database, runtime_table: RuntimeTable, clock: FakeClock) -> Rig:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    metrics = AiMetrics(MeterProvider(metric_readers=[reader]))
    tracer = tracer_provider.get_tracer("test")
    model = FakeProvider()
    explainer = Explainer(
        database=database.app_triage,
        provider=model,
        budget=AiBudget(runtime_table.client, runtime_table.name, clock),
        metrics=metrics,
        tracer=tracer,
    )
    worker = TriageWorker(explainer=explainer, clock=clock, metrics=metrics, tracer=tracer)
    return Rig(worker, model, spans, reader)


def message(
    org_id: UUID | str,
    finding_id: UUID | str,
    *,
    receive_count: int = 1,
    message_id: str = "m-1",
    traceparent: str | None = UPLOAD_TRACEPARENT,
) -> dict[str, Any]:
    """An SQS record as Lambda delivers it, on its `receive_count`th delivery."""
    attributes = {"traceparent": {"stringValue": traceparent, "dataType": "String"}}
    return {
        "messageId": message_id,
        "body": json.dumps({"org_id": str(org_id), "finding_id": str(finding_id)}),
        "attributes": {
            "SentTimestamp": "1790596740000",
            "ApproximateReceiveCount": str(receive_count),
        },
        "messageAttributes": attributes if traceparent else {},
    }


def statuses(database: Database, tenant: Tenant) -> list[tuple[str, str | None]]:
    with database.admin.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT status, error_code FROM ai_analyses WHERE finding_id = :finding "
                "AND id <> :seeded"
            ),
            {"finding": tenant.finding_id, "seeded": tenant.analysis_id},
        ).all()
    return [tuple(row) for row in rows]


def test_a_queued_finding_is_explained_and_its_message_done(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    retry = rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id)])

    assert retry == []
    assert statuses(database, tenant) == [("succeeded", None)]


def test_a_passing_bedrock_failure_is_handed_back_to_sqs(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_throttled")]

    retry = rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id, receive_count=2)])

    assert retry == ["m-1"]
    assert statuses(database, tenant) == []


def test_on_the_last_delivery_a_passing_failure_is_stored(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ProviderError("provider_throttled")]

    retry = rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id, receive_count=3)])

    assert retry == []
    assert statuses(database, tenant) == [("failed", "provider_throttled")]


def test_a_finding_that_is_gone_is_let_go(database: Database, rig: Rig, logs: io.StringIO) -> None:
    tenant = add_tenant(database.admin)

    retry = rig.worker.handle_batch([message(tenant.org_id, uuid7())])

    assert retry == []
    assert rig.model.calls == []
    assert '"reason": "finding_gone"' in logs.getvalue()


@pytest.mark.parametrize(
    "body", ["not json", "[]", '{"org_id": "x", "finding_id": "y"}', '{"org_id": null}']
)
def test_a_malformed_message_is_let_go(rig: Rig, logs: io.StringIO, body: str) -> None:
    record = message(uuid7(), uuid7()) | {"body": body}

    assert rig.worker.handle_batch([record]) == []
    assert '"reason": "malformed"' in logs.getvalue()


class _Broken:
    def explain(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("could not store 203.0.113.9")


def test_an_unexpected_error_is_handed_back_and_logged_by_type_only(
    rig: Rig, logs: io.StringIO
) -> None:
    broken = replace(rig.worker, explainer=_Broken())

    retry = broken.handle_batch([message(uuid7(), uuid7())])

    assert retry == ["m-1"]
    assert '"error_code": "RuntimeError"' in logs.getvalue()
    assert "203.0.113.9" not in logs.getvalue()
    [span] = rig.spans.get_finished_spans()
    assert (span.status.status_code, span.status.description) == (StatusCode.ERROR, "RuntimeError")


def test_each_message_of_a_batch_stands_alone(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    retry = rig.worker.handle_batch(
        [
            message(tenant.org_id, tenant.finding_id, message_id="good"),
            message("x", "y", message_id="malformed"),
        ]
    )

    assert retry == []
    assert statuses(database, tenant) == [("succeeded", None)]


def test_the_explain_span_links_to_the_upload_and_holds_the_model_call(
    database: Database, rig: Rig
) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id)])

    spans = {span.name: span for span in rig.spans.get_finished_spans()}
    explain, generate = spans["triage.explain"], spans["triage.generate"]
    assert [format(link.context.trace_id, "032x") for link in explain.links] == [
        "0af7651916cd43dd8448eb211c80319c"
    ]
    assert generate.parent is not None
    assert generate.parent.span_id == explain.context.span_id
    assert explain.attributes == {"nettriage.ai.outcome": "succeeded"}


def test_the_time_a_message_waited_is_recorded(database: Database, rig: Rig) -> None:
    tenant = add_tenant(database.admin)
    rig.model.script = [ANSWER]

    rig.worker.handle_batch([message(tenant.org_id, tenant.finding_id)])

    data = rig.metrics.get_metrics_data()
    assert data is not None
    points = [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "nettriage.queue.message.age"
        for point in metric.data.data_points
    ]
    assert len(points) == 1
    assert isinstance(points[0], HistogramDataPoint)
    assert (points[0].sum, dict(points[0].attributes or {})) == (60.0, {"queue": "triage"})
