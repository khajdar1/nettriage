"""The current OpenTelemetry trace and span IDs as hex strings, and the W3C traceparent."""

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def current_span_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.span_id, "016x") if context.is_valid else None


def current_traceparent() -> str | None:
    """The current span as a W3C `traceparent`, for work that continues after an async hop: the
    upload's S3 metadata, then the queue message (spec §9.1)."""
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")
