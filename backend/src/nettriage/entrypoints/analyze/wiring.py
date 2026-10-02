"""Building the analyze worker in Lambda, once per cold start (spec §6.8): its database URL
comes from SSM, findings to explain go to the triage queue, and its telemetry goes to the
providers the handler set up (spec §9.1)."""

from __future__ import annotations

import boto3
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.triage_queue import TriageQueue
from nettriage.adapters.upload_objects import UploadObjects
from nettriage.application.clock import system_clock
from nettriage.entrypoints.analyze.worker import Worker
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AnalyzeMetrics


def build_worker(
    settings: Settings,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    session: boto3.session.Session | None = None,
) -> Worker:
    session = session or boto3.session.Session()
    values = read_parameters(
        session.client("ssm", config=AWS_CONFIG), [settings.database_url_parameter]
    )

    def flush() -> None:
        # Lambda may freeze the environment right after the handler returns.
        tracer_provider.force_flush()
        meter_provider.force_flush()

    return Worker(
        database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        objects=UploadObjects(session.client("s3", config=AWS_CONFIG), settings.uploads_bucket),
        triage=TriageQueue(session.client("sqs", config=AWS_CONFIG), settings.triage_queue_url),
        clock=system_clock,
        metrics=AnalyzeMetrics(meter_provider),
        tracer=tracer_provider.get_tracer("nettriage.analyze"),
        flush=flush,
    )
