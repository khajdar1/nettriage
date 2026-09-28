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
        self.rate_limit_errors = meter.create_counter(
            "nettriage.ratelimit.errors",
            description="Rate-limit checks that failed and let the request through, by policy",
        )
