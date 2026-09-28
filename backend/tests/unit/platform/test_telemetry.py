from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.telemetry import create_meter_provider, create_tracer_provider

SETTINGS = Settings(stage="local", version="9.9.9")


def _client(
    services: Services,
    tracer_provider: TracerProvider,
    reader: InMemoryMetricReader | None = None,
) -> TestClient:
    meter_provider = create_meter_provider(SETTINGS, reader or InMemoryMetricReader())
    app = create_app(
        SETTINGS, services, tracer_provider=tracer_provider, meter_provider=meter_provider
    )
    return TestClient(app)


def _server_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.kind == SpanKind.SERVER]


def test_requests_produce_a_server_span_with_resource_attributes(services: Services) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = create_tracer_provider(SETTINGS, exporter)

    _client(services, tracer_provider).get("/api/health")
    tracer_provider.force_flush()

    [span] = _server_spans(exporter)
    assert span.attributes is not None
    assert span.attributes.get("http.route") == "/api/health"
    assert span.resource.attributes["service.name"] == "nettriage-api"
    assert span.resource.attributes["service.version"] == "9.9.9"
    assert span.resource.attributes["deployment.environment.name"] == "local"


def test_problem_details_carry_the_request_trace_id(services: Services) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = create_tracer_provider(SETTINGS, exporter)

    body = _client(services, tracer_provider).get("/api/nope").json()
    tracer_provider.force_flush()

    [span] = _server_spans(exporter)
    assert body["trace_id"] == format(span.context.trace_id, "032x")


def test_http_server_metrics_are_recorded(services: Services) -> None:
    reader = InMemoryMetricReader()

    tracer_provider = create_tracer_provider(SETTINGS, InMemorySpanExporter())
    _client(services, tracer_provider, reader).get("/api/health")

    data = reader.get_metrics_data()
    assert data is not None
    names = {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    assert names & {"http.server.request.duration", "http.server.duration"}


class FailingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("telemetry backend unreachable")

    def shutdown(self) -> None:
        return None


def test_requests_succeed_when_the_telemetry_backend_is_down(
    monkeypatch: pytest.MonkeyPatch, services: Services
) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "nettriage-test-api")  # synchronous export

    tracer_provider = create_tracer_provider(SETTINGS, FailingExporter())
    response = _client(services, tracer_provider).get("/api/health")

    assert response.status_code == 200
