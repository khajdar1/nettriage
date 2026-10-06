"""The `ops` function's jobs (Plan 7a §2, §6), with real Postgres, DynamoDB and S3 in moto, and the
Postgres programs stood in for. Each job answers with counts only, records
`nettriage.ops.runs` {job, outcome} and one span, and a failure is logged and traced by its type,
never its message."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import boto3
import httpx
import pytest
from conftest import REGION, Database, FakeClock, RuntimeTable
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, NumberDataPoint
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from tenantdata import add_tenant

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.pg_client import DumpFailed, Target
from nettriage.adapters.postgres import create_database_engine
from nettriage.entrypoints.ops.drill import DrillFailed
from nettriage.entrypoints.ops.jobs import JobFailed, Ops
from nettriage.platform.metrics import OpsMetrics

APP = "https://d1234.cloudfront.net"
BUCKET = "nettriage-test-backups"


class FakePgDump:
    def __init__(self) -> None:
        self.fail = False

    def version(self) -> str:
        return "17.11"

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        if self.fail:
            raise DumpFailed("pg_dump exited with status 1")
        out.write_bytes(b"PGDMP")


class FakeServer:
    """Restores exactly what the manifest counted, unless told to lose a row."""

    def __init__(self, home: Path, lose_a_row: bool) -> None:
        self.home = home
        self.lose_a_row = lose_a_row
        self.dump: Path | None = None

    def __enter__(self) -> FakeServer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def prepare(self, roles: list[str]) -> None:
        return None

    def restore(self, dump: Path) -> None:
        self.dump = dump

    def counts(self) -> dict[str, int]:
        manifest = json.loads((self.home.parent / "expected.json").read_text(encoding="utf-8"))
        counts: dict[str, int] = dict(manifest)
        if self.lose_a_row:
            counts["organizations"] -= 1
        return counts


@dataclass
class Rig:
    ops: Ops
    health: list[httpx.Response]
    pg_dump: FakePgDump
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader
    store: BackupStore
    workdir: Path
    lose_a_row: list[bool]


@pytest.fixture
def rig(database: Database, runtime_table: RuntimeTable, clock: FakeClock, tmp_path: Path) -> Rig:
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"})
    store = BackupStore(s3, BUCKET)
    health = [httpx.Response(200, json={"status": "ok", "version": "abc"})]
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    pg_dump = FakePgDump()
    lose_a_row = [False]
    workdir = tmp_path / "tmp"
    workdir.mkdir()

    def throwaway(home: Path) -> FakeServer:
        latest = store.latest()
        assert latest is not None
        manifest = store.fetch(latest, tmp_path / "peek.dump")
        (home.parent / "expected.json").write_text(json.dumps(manifest.tables), encoding="utf-8")
        return FakeServer(home, lose_a_row[0])

    ops = Ops(
        http=httpx.Client(transport=httpx.MockTransport(lambda request: health[0])),
        app_url=APP,
        ops_database=database.app_ops,
        backup_database=database.app_backup,
        dynamodb=runtime_table.client,
        runtime_table=runtime_table.name,
        store=store,
        pg_dump=pg_dump,
        throwaway=throwaway,
        clock=clock,
        metrics=OpsMetrics(MeterProvider(metric_readers=[reader])),
        tracer=tracer_provider.get_tracer("test"),
        workdir=workdir,
    )
    return Rig(ops, health, pg_dump, spans, reader, store, workdir, lose_a_row)


def points(reader: InMemoryMetricReader, name: str) -> dict[tuple[tuple[str, str], ...], float]:
    data = reader.get_metrics_data()
    found: dict[tuple[tuple[str, str], ...], float] = {}
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if isinstance(point, NumberDataPoint):
                        key = tuple(
                            sorted((k, str(v)) for k, v in (point.attributes or {}).items())
                        )
                        found[key] = point.value
    return found


def run_counted(rig: Rig, job: str, outcome: str) -> float:
    return points(rig.metrics, "nettriage.ops.runs").get((("job", job), ("outcome", outcome)), 0)


def test_a_healthy_app_is_probed_as_a_success(rig: Rig) -> None:
    result = rig.ops.run("probe")

    assert result == {"job": "probe", "ok": True}
    assert points(rig.metrics, "nettriage.probe.success") == {(("check", "health"),): 1}
    assert run_counted(rig, "probe", "success") == 1


def test_an_unhealthy_app_is_a_failed_probe_without_an_error(rig: Rig) -> None:
    rig.health[0] = httpx.Response(503)

    result = rig.ops.run("probe")

    assert result == {"job": "probe", "ok": False}
    assert points(rig.metrics, "nettriage.probe.success") == {(("check", "health"),): 0}
    assert run_counted(rig, "probe", "failure") == 1


def test_the_hourly_check_reaches_the_database_and_the_runtime_table(rig: Rig) -> None:
    result = rig.ops.run("check")

    assert result == {"job": "check", "database": True, "dynamodb": True}
    assert points(rig.metrics, "nettriage.probe.success") == {
        (("check", "database"),): 1,
        (("check", "dynamodb"),): 1,
    }


def test_the_cleanup_answers_with_its_counts(rig: Rig) -> None:
    result = rig.ops.run("cleanup")

    assert set(result) == {
        "job",
        "uploads_expired",
        "analyses_failed",
        "invitations_deleted",
        "audit_rows_deleted",
    }
    assert run_counted(rig, "cleanup", "success") == 1


def test_the_backup_answers_with_its_day_tables_and_rows(rig: Rig, database: Database) -> None:
    add_tenant(database.admin)

    result = rig.ops.run("backup")

    assert result["job"] == "backup"
    assert result["day"] == "2026-09-28"
    assert int(result["tables"]) >= 14
    assert int(result["rows"]) >= 1
    [span] = rig.spans.get_finished_spans()
    assert span.name == "ops.backup"
    assert span.attributes is not None
    assert span.attributes["ops.tables"] == result["tables"]
    assert run_counted(rig, "backup", "success") == 1


def test_a_failed_backup_fails_the_run_and_reports_its_type_only(
    rig: Rig, logs: io.StringIO
) -> None:
    rig.pg_dump.fail = True

    with pytest.raises(JobFailed) as failure:
        rig.ops.run("backup")

    assert str(failure.value) == "DumpFailed"
    assert run_counted(rig, "backup", "failure") == 1
    [span] = rig.spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "DumpFailed"
    [line] = [json.loads(line) for line in logs.getvalue().splitlines() if "ops_job_failed" in line]
    assert (line["job"], line["error_code"]) == ("backup", "DumpFailed")
    assert "status 1" not in logs.getvalue()


def test_the_drill_restores_the_newest_backup_and_answers_with_its_counts(
    rig: Rig, database: Database
) -> None:
    add_tenant(database.admin)
    backup = rig.ops.run("backup")

    result = rig.ops.run("restore_drill")

    assert result == {
        "job": "restore_drill",
        "day": backup["day"],
        "tables": backup["tables"],
        "rows": backup["rows"],
    }
    assert run_counted(rig, "restore_drill", "success") == 1


def test_a_drill_that_finds_a_difference_fails_the_run(rig: Rig, database: Database) -> None:
    add_tenant(database.admin)
    rig.ops.run("backup")
    rig.lose_a_row[0] = True

    with pytest.raises(DrillFailed):
        rig.ops.run("restore_drill")

    assert run_counted(rig, "restore_drill", "failure") == 1


def test_an_error_that_could_quote_the_database_leaves_by_its_type_only(
    rig: Rig, database: Database
) -> None:
    """Lambda logs what the handler raises, and a database error can quote connection details."""
    gone = database.app_ops.url.set(database="nettriage_gone")
    rig.ops.ops_database = create_database_engine(
        gone.render_as_string(hide_password=False), pool_size=1
    )

    with pytest.raises(JobFailed) as failure:
        rig.ops.run("cleanup")

    assert str(failure.value) == "OperationalError"
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__
    assert run_counted(rig, "cleanup", "failure") == 1


def test_an_unknown_job_is_refused(rig: Rig) -> None:
    with pytest.raises(ValueError, match="unknown job"):
        rig.ops.run("defrag")
