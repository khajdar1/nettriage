"""The `ops` function's jobs (Plan 7a §2): `probe` every 5 minutes, `check` hourly, `backup`
nightly, `cleanup` daily and `restore_drill` weekly, each named by the schedule that invokes it.

Every job answers with counts only, records `nettriage.ops.runs` {job, outcome} and one span
`ops.<job>`. A probe or check that fails is a "failure" outcome but not an error: the next run is
minutes away. A backup, cleanup or drill that fails raises, so Lambda retries it, and is logged
and traced by its type only (spec §9.3)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from sqlalchemy import Engine

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.maintenance import clean_up
from nettriage.adapters.probes import database_answers, dynamodb_answers, health_answers
from nettriage.application.clock import Clock
from nettriage.entrypoints.ops.backup import Dumper, run_backup
from nettriage.entrypoints.ops.drill import Server, run_restore_drill
from nettriage.platform.metrics import OpsMetrics

if TYPE_CHECKING:
    from types_boto3_dynamodb.client import DynamoDBClient

logger = logging.getLogger(__name__)

type Result = dict[str, str | int | bool]


@dataclass
class Ops:
    http: httpx.Client
    app_url: str
    ops_database: Engine
    backup_database: Engine
    dynamodb: DynamoDBClient
    runtime_table: str
    store: BackupStore
    pg_dump: Dumper
    throwaway: Callable[[Path], Server]
    clock: Clock
    metrics: OpsMetrics
    tracer: Tracer
    workdir: Path = Path("/tmp")  # noqa: S108 - Lambda's own scratch space
    flush: Callable[[], None] = field(default=lambda: None)

    def run(self, job: str) -> Result:
        jobs: dict[str, Callable[[], tuple[Result, bool]]] = {
            "probe": self._probe,
            "check": self._check,
            "backup": self._backup,
            "cleanup": self._cleanup,
            "restore_drill": self._restore_drill,
        }
        if job not in jobs:
            raise ValueError(f"unknown job {job!r}")
        with self._span(f"ops.{job}") as span:
            try:
                answer, ok = jobs[job]()
            except Exception as error:
                self.metrics.runs.add(1, {"job": job, "outcome": "failure"})
                logger.warning(
                    "ops_job_failed", extra={"job": job, "error_code": type(error).__name__}
                )
                raise
            outcome = "success" if ok else "failure"
            for key, value in answer.items():
                span.set_attribute(f"ops.{key}", value)
            self.metrics.runs.add(1, {"job": job, "outcome": outcome})
            logger.info("ops_job_done", extra={"job": job, "outcome": outcome, **answer})
        return {"job": job, **answer}

    def _probe(self) -> tuple[Result, bool]:
        ok = health_answers(self.http, self.app_url)
        self.metrics.probe_success.set(int(ok), {"check": "health"})
        return {"ok": ok}, ok

    def _check(self) -> tuple[Result, bool]:
        database = database_answers(self.ops_database)
        dynamodb = dynamodb_answers(self.dynamodb, self.runtime_table)
        self.metrics.probe_success.set(int(database), {"check": "database"})
        self.metrics.probe_success.set(int(dynamodb), {"check": "dynamodb"})
        return {"database": database, "dynamodb": dynamodb}, database and dynamodb

    def _backup(self) -> tuple[Result, bool]:
        now = self.clock()
        manifest = run_backup(self.backup_database, self.pg_dump, self.store, now, self.workdir)
        return {
            "day": now.date().isoformat(),
            "tables": len(manifest.tables),
            "rows": sum(manifest.tables.values()),
        }, True

    def _cleanup(self) -> tuple[Result, bool]:
        counts = clean_up(self.ops_database)
        return {
            "uploads_expired": counts.uploads_expired,
            "analyses_failed": counts.analyses_failed,
            "invitations_deleted": counts.invitations_deleted,
            "audit_rows_deleted": counts.audit_rows_deleted,
        }, True

    def _restore_drill(self) -> tuple[Result, bool]:
        result = run_restore_drill(self.store, self.throwaway, self.workdir)
        return {"day": result.day, "tables": result.tables, "rows": result.rows}, True

    @contextmanager
    def _span(self, name: str) -> Iterator[Span]:
        """A failure is recorded by the error's type only: OpenTelemetry would otherwise copy
        its message, which could quote data, into the trace."""
        with self.tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                yield span
            except Exception as error:
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
                raise
