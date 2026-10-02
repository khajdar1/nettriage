"""Building the triage worker in Lambda, once per cold start (spec §6.8): its database URL comes
from SSM, budgets live in the runtime table, the model is called through Bedrock in its own
Region, and the `ai_enabled` switch is re-read from SSM every minute (spec §9.7)."""

from __future__ import annotations

import boto3
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.ai_budget import AiBudget
from nettriage.adapters.bedrock_llm import BEDROCK_CONFIG, BedrockProvider
from nettriage.adapters.kill_switch import KillSwitch, ssm_parameter
from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.clock import system_clock
from nettriage.entrypoints.triage.explainer import Explainer
from nettriage.entrypoints.triage.worker import TriageWorker
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AiMetrics


def build_worker(
    settings: Settings,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    session: boto3.session.Session | None = None,
) -> TriageWorker:
    session = session or boto3.session.Session()
    ssm = session.client("ssm", config=AWS_CONFIG)
    values = read_parameters(ssm, [settings.database_url_parameter])
    metrics = AiMetrics(meter_provider)
    tracer = tracer_provider.get_tracer("nettriage.triage")
    bedrock = session.client(
        "bedrock-runtime", region_name=settings.bedrock_region, config=BEDROCK_CONFIG
    )
    explainer = Explainer(
        database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        provider=BedrockProvider(bedrock, settings.bedrock_model_id),
        budget=AiBudget(
            session.client("dynamodb", config=AWS_CONFIG), settings.runtime_table, system_clock
        ),
        metrics=metrics,
        tracer=tracer,
        enabled=KillSwitch(ssm_parameter(ssm, settings.ai_enabled_parameter), system_clock).is_on,
    )

    def flush() -> None:
        # Lambda may freeze the environment right after the handler returns.
        tracer_provider.force_flush()
        meter_provider.force_flush()

    return TriageWorker(
        explainer=explainer, clock=system_clock, metrics=metrics, tracer=tracer, flush=flush
    )
