"""The nightly backup job (Plan 7a §2.3), against real Postgres as `app_backup`, with S3 in moto
and `pg_dump` stood in for: it dumps the snapshot it counted, then stores the dump and a manifest
of those counts under the day's name, and leaves nothing behind locally."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from conftest import REGION, Database
from tenantdata import add_tenant

from nettriage.adapters.backups import BackupStore
from nettriage.adapters.pg_client import DumpFailed, Target
from nettriage.entrypoints.ops.backup import run_backup

BUCKET = "nettriage-test-backups"
NIGHT = datetime(2026, 10, 7, 2, 0, 4, tzinfo=UTC)


class FakePgDump:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[Target, str, Path]] = []

    def version(self) -> str:
        return "17.11"

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        self.calls.append((target, snapshot, out))
        if self.fail:
            raise DumpFailed("pg_dump exited with status 1")
        out.write_bytes(b"PGDMP custom archive")


@pytest.fixture
def store() -> Iterator[BackupStore]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        yield BackupStore(client, BUCKET)


def test_a_backup_dumps_the_counted_snapshot_and_stores_it_under_the_day(
    database: Database, store: BackupStore, tmp_path: Path
) -> None:
    add_tenant(database.admin)
    pg_dump = FakePgDump()

    manifest = run_backup(database.app_backup, pg_dump, store, NIGHT, tmp_path)

    [(target, snapshot, _)] = pg_dump.calls
    assert target.user == "app_backup"
    assert snapshot
    assert store.latest() == "2026-10-07"
    fetched = store.fetch("2026-10-07", tmp_path / "check.dump")
    assert fetched == manifest
    assert (manifest.created_at, manifest.client_version) == (NIGHT.isoformat(), "17.11")
    assert manifest.tables["organizations"] >= 1
    assert "app_backup" in manifest.roles
    assert (tmp_path / "check.dump").read_bytes() == b"PGDMP custom archive"


def test_the_local_dump_is_removed_once_stored(
    database: Database, store: BackupStore, tmp_path: Path
) -> None:
    run_backup(database.app_backup, FakePgDump(), store, NIGHT, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_failed_dump_stores_nothing(
    database: Database, store: BackupStore, tmp_path: Path
) -> None:
    with pytest.raises(DumpFailed):
        run_backup(database.app_backup, FakePgDump(fail=True), store, NIGHT, tmp_path)

    assert store.latest() is None
    assert list(tmp_path.iterdir()) == []
