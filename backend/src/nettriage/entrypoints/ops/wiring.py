"""Building the `ops` function in Lambda, once per cold start (Plan 7a §2, §4): `app_ops`'s
pooled URL and `app_backup`'s direct one come from SSM, the backups land in the stage's bucket,
the hourly check reads the runtime table, and the Postgres programs come from the layer."""

from __future__ import annotations

import boto3
import httpx
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.parameters import AWS_CONFIG, read_parameters
from nettriage.adapters.pg_client import PgClient, ThrowawayServer
from nettriage.adapters.postgres import create_database_engine
from nettriage.application.clock import system_clock
from nettriage.entrypoints.ops.jobs import Ops
from nettriage.platform.config import Settings
from nettriage.platform.metrics import OpsMetrics


def build_ops(
    settings: Settings,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider,
    session: boto3.session.Session | None = None,
) -> Ops:
    session = session or boto3.session.Session()
    names = [settings.database_url_parameter, settings.backup_database_url_parameter]
    values = read_parameters(session.client("ssm", config=AWS_CONFIG), names)
    pg = PgClient()

    def flush() -> None:
        # Lambda may freeze the environment right after the handler returns.
        tracer_provider.force_flush()
        meter_provider.force_flush()

    return Ops(
        http=httpx.Client(),
        app_url=settings.app_url,
        ops_database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        backup_database=create_database_engine(
            values[settings.backup_database_url_parameter], pool_size=1
        ),
        dynamodb=session.client("dynamodb", config=AWS_CONFIG),
        runtime_table=settings.runtime_table,
        store=BackupStore(session.client("s3", config=AWS_CONFIG), settings.backups_bucket),
        pg_dump=pg,
        throwaway=lambda home: ThrowawayServer(pg, home),
        clock=system_clock,
        metrics=OpsMetrics(meter_provider),
        tracer=tracer_provider.get_tracer("nettriage.ops"),
        flush=flush,
    )
