"""The app's own counters (spec §9.2). Attributes never carry user IDs, org IDs, IPs or free
text, which keeps Grafana's free tier under its series limit."""

from opentelemetry.metrics import MeterProvider, get_meter_provider


class AppMetrics:
    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        meter = (meter_provider or get_meter_provider()).get_meter("nettriage")
        self.signups = meter.create_counter(
            "nettriage.signups", description="Users who signed in for the first time"
        )
        self.csrf_failed = meter.create_counter(
            "nettriage.csrf.failed", description="State-changing requests refused by CSRF checks"
        )
        self.rate_limited = meter.create_counter(
            "nettriage.ratelimit.limited", description="Requests refused with 429, by policy"
        )
        self.authz_denied = meter.create_counter(
            "nettriage.authz.denied", description="Requests refused by authorization, by permission"
        )
        self.rate_limit_errors = meter.create_counter(
            "nettriage.ratelimit.errors",
            description="Rate-limit checks that failed and let the request through, by policy",
        )


class AnalyzeMetrics:
    """The analyze worker's metrics (spec §9.2)."""

    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        meter = (meter_provider or get_meter_provider()).get_meter("nettriage")
        self.uploads_processed = meter.create_counter(
            "nettriage.uploads.processed",
            description="Upload events handled, by outcome: analyzed, failed, ignored, duplicate",
        )
        self.processing_duration = meter.create_histogram(
            "nettriage.upload.processing.duration",
            unit="s",
            description="Time to parse, detect and store one upload",
        )
        self.rows_parsed = meter.create_counter(
            "nettriage.rows.parsed", description="Flow records parsed"
        )
        self.rows_rejected = meter.create_counter(
            "nettriage.rows.rejected", description="Lines that weren't valid flow records"
        )
        self.findings_created = meter.create_counter(
            "nettriage.findings.created", description="Findings stored, by detector and severity"
        )
        self.upload_rejected = meter.create_counter(
            "nettriage.upload.rejected", description="Uploads that failed analysis, by reason"
        )
        self.queue_message_age = meter.create_histogram(
            "nettriage.queue.message.age",
            unit="s",
            description="How long a message waited in its queue, by queue",
        )


class AiMetrics:
    """The model calls' metrics (spec §9.2): OpenTelemetry's GenAI client metrics, and NetTriage's
    cost, outcome and cache counters. Attributes name the provider and model, never an org."""

    def __init__(self, meter_provider: MeterProvider | None = None) -> None:
        meter = (meter_provider or get_meter_provider()).get_meter("nettriage")
        self.token_usage = meter.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Tokens per model call, by type: input or output",
        )
        self.operation_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s", description="Time per model call"
        )
        self.cost_usd = meter.create_counter(
            "nettriage.ai.cost.usd", unit="USD", description="What model calls cost"
        )
        self.outcomes = meter.create_counter(
            "nettriage.ai.outcome",
            description="Explanations by outcome: succeeded, cached, failed, skipped_budget, "
            "invalid_output",
        )
        self.cache_hits = meter.create_counter(
            "nettriage.ai.cache.hits", description="Explanations served from a stored analysis"
        )
        self.queue_message_age = meter.create_histogram(
            "nettriage.queue.message.age",
            unit="s",
            description="How long a message waited in its queue, by queue",
        )
