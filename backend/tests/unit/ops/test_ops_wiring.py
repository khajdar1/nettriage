"""Building the `ops` function in Lambda (Plan 7a §2, §4): its two connection strings from SSM
(`app_ops` through the pooler, `app_backup` direct), the backups bucket, the runtime table, the
app's URL for the probe, and the layer's Postgres programs. The handler runs the job the
schedule names and flushes telemetry whatever happens."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from nettriage.adapters.parameters import MissingParameterError
from nettriage.adapters.pg_client import LAYER_BIN, PgClient
from nettriage.entrypoints.ops import handler
from nettriage.entrypoints.ops.wiring import build_ops
from nettriage.platform.config import Settings

SETTINGS = Settings(
    stage="dev",
    service_name="nettriage-ops",
    database_url_parameter="/nettriage/dev/db/app-ops-url",
    backup_database_url_parameter="/nettriage/dev/db/app-backup-url",
    runtime_table="nettriage-dev-runtime",
    backups_bucket="nettriage-dev-backups-1a2b3c4d",
    app_url="https://d1234.cloudfront.net",
)


@pytest.fixture
def session() -> Iterator[boto3.session.Session]:
    with mock_aws():
        yield boto3.session.Session(region_name="eu-north-1")


def store_urls(session: boto3.session.Session) -> None:
    ssm = session.client("ssm")
    ssm.put_parameter(
        Name="/nettriage/dev/db/app-ops-url",
        Value="postgresql://app_ops:pw@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )
    ssm.put_parameter(
        Name="/nettriage/dev/db/app-backup-url",
        Value="postgresql://app_backup:pw@ep-x.eu-central-1.aws.neon.tech/neondb",
        Type="SecureString",
    )


def test_the_jobs_run_as_their_own_roles_against_the_stages_resources(
    session: boto3.session.Session,
) -> None:
    store_urls(session)

    ops = build_ops(SETTINGS, TracerProvider(), MeterProvider(), session)

    assert ops.ops_database.url.username == "app_ops"
    assert ops.backup_database.url.username == "app_backup"
    assert ops.backup_database.url.host == "ep-x.eu-central-1.aws.neon.tech"
    assert ops.store.bucket == "nettriage-dev-backups-1a2b3c4d"
    assert ops.runtime_table == "nettriage-dev-runtime"
    assert ops.app_url == "https://d1234.cloudfront.net"
    assert isinstance(ops.pg_dump, PgClient)
    assert ops.pg_dump.bin_dir == LAYER_BIN
    assert ops.workdir == Path("/tmp")  # noqa: S108 - Lambda's scratch space
    ops.flush()


def test_a_missing_connection_string_is_named(session: boto3.session.Session) -> None:
    with pytest.raises(MissingParameterError, match="app-backup-url"):
        build_ops(SETTINGS, TracerProvider(), MeterProvider(), session)


class FakeOps:
    def __init__(self) -> None:
        self.jobs: list[str] = []
        self.flushed = 0

    def run(self, job: str) -> dict[str, str | int | bool]:
        self.jobs.append(job)
        if job == "backup":
            raise RuntimeError("dump failed")
        return {"job": job, "ok": True}

    def flush(self) -> None:
        self.flushed += 1


def test_the_handler_runs_the_scheduled_job_and_answers_with_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOps()
    monkeypatch.setattr(handler, "ops", lambda: fake)

    response = handler.handle({"job": "probe"}, object())

    assert response == {"job": "probe", "ok": True}
    assert (fake.jobs, fake.flushed) == (["probe"], 1)


def test_a_failed_job_still_flushes_its_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOps()
    monkeypatch.setattr(handler, "ops", lambda: fake)

    with pytest.raises(RuntimeError):
        handler.handle({"job": "backup"}, object())

    assert fake.flushed == 1


def test_an_event_without_a_job_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOps()
    monkeypatch.setattr(handler, "ops", lambda: fake)

    with pytest.raises(ValueError, match="job"):
        handler.handle({}, object())
