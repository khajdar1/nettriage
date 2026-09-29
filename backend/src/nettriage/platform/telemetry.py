"""OpenTelemetry providers and FastAPI instrumentation.

Only `install_global_providers` touches global state, and only the production
entrypoint calls it. Tests pass in-memory exporters and readers instead.
"""

from typing import Any

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span

from nettriage.platform.config import Settings


def build_resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.version,
            "deployment.environment.name": settings.stage,
        }
    )


def create_tracer_provider(
    settings: Settings, exporter: SpanExporter | None = None
) -> TracerProvider:
    span_exporter = exporter or OTLPSpanExporter()
    # Lambda can freeze the environment right after a response, so in Lambda each span
    # goes straight to the local collector extension, which forwards it asynchronously.
    processor: SpanProcessor = (
        SimpleSpanProcessor(span_exporter)
        if settings.running_in_lambda
        else BatchSpanProcessor(span_exporter)
    )
    provider = TracerProvider(resource=build_resource(settings))
    provider.add_span_processor(processor)
    return provider


def create_meter_provider(settings: Settings, reader: MetricReader | None = None) -> MeterProvider:
    metric_reader = reader or PeriodicExportingMetricReader(
        OTLPMetricExporter(), export_interval_millis=10_000
    )
    return MeterProvider(resource=build_resource(settings), metric_readers=[metric_reader])


def install_global_providers(
    tracer_provider: TracerProvider, meter_provider: MeterProvider
) -> None:
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)


def hide_sign_in_query(span: Span, scope: dict[str, Any]) -> None:
    """The sign-in callback's query holds Cognito's authorization code and the sign-in state.
    Spans of /api/auth/* keep their URL without it."""
    if not span.is_recording() or not str(scope.get("path", "")).startswith("/api/auth/"):
        return
    attributes = getattr(span, "attributes", None) or {}
    for name in ("http.url", "http.target", "url.full"):
        value = attributes.get(name)
        if isinstance(value, str) and "?" in value:
            span.set_attribute(name, value.split("?", 1)[0])
    if "url.query" in attributes:
        span.set_attribute("url.query", "")


def instrument_app(
    app: FastAPI, tracer_provider: TracerProvider, meter_provider: MeterProvider | None
) -> None:
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        server_request_hook=hide_sign_in_query,
    )
