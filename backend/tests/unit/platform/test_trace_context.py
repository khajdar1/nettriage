"""The W3C traceparent that carries a trace across async hops (spec §9.1)."""

from opentelemetry.sdk.trace import TracerProvider

from nettriage.platform.trace_context import current_traceparent


def test_outside_a_span_there_is_no_traceparent() -> None:
    assert current_traceparent() is None


def test_a_spans_traceparent_names_its_trace_and_span() -> None:
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("upload") as span:
        context = span.get_span_context()
        value = current_traceparent()

    flags = int(context.trace_flags)
    assert value == f"00-{context.trace_id:032x}-{context.span_id:016x}-{flags:02x}"
    assert flags & 0x01  # sampled
