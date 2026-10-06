"""The weekly restore drill (Plan 7a §2.5), with S3 in moto and the throwaway server stood in
for: it restores the newest backup and checks every table's rows against the manifest, fails
naming the tables that differ (never their rows), and leaves nothing behind."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from conftest import REGION

from nettriage.adapters.backups import BackupStore, Manifest
from nettriage.entrypoints.ops.drill import DrillFailed, NoBackup, run_restore_drill

BUCKET = "nettriage-test-backups"
TABLES = {"organizations": 2, "uploads": 7, "audit_log": 40}


class FakeServer:
    def __init__(self, home: Path, restored: dict[str, int]) -> None:
        self.home = home
        self.restored = restored
        self.prepared: list[str] | None = None
        self.dump: Path | None = None
        self.stopped = False

    def __enter__(self) -> FakeServer:
        self.home.mkdir(parents=True)
        (self.home / "data").mkdir()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stopped = True

    def prepare(self, roles: list[str]) -> None:
        self.prepared = roles

    def restore(self, dump: Path) -> None:
        self.dump = dump
        assert dump.read_bytes() == b"PGDMP night two"

    def counts(self) -> dict[str, int]:
        return self.restored


@pytest.fixture
def store() -> Iterator[BackupStore]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        yield BackupStore(client, BUCKET)


def backed_up(store: BackupStore, tmp_path: Path) -> None:
    for day, body in (("2026-10-06", b"PGDMP night one"), ("2026-10-07", b"PGDMP night two")):
        dump = tmp_path / f"{day}.dump"
        dump.write_bytes(body)
        store.put(
            day,
            dump,
            Manifest(
                created_at=f"{day}T02:00:00+00:00",
                server_version="17.5",
                client_version="17.11",
                tables=TABLES,
                roles=["app_backup", "app_ops"],
            ),
        )
        dump.unlink()


def test_the_newest_backup_is_restored_and_matches_its_manifest(
    store: BackupStore, tmp_path: Path
) -> None:
    backed_up(store, tmp_path)
    servers: list[FakeServer] = []

    def throwaway(home: Path) -> FakeServer:
        servers.append(FakeServer(home, dict(TABLES)))
        return servers[-1]

    result = run_restore_drill(store, throwaway, tmp_path)

    assert (result.day, result.tables, result.rows) == ("2026-10-07", 3, 49)
    [server] = servers
    assert server.prepared == ["app_backup", "app_ops"]
    assert server.stopped


@pytest.mark.parametrize(
    "restored",
    [
        {"organizations": 2, "uploads": 6, "audit_log": 40},
        {"organizations": 2, "audit_log": 40},
        {**TABLES, "stray": 1},
    ],
)
def test_a_restore_that_differs_from_its_manifest_fails_naming_the_tables(
    store: BackupStore, tmp_path: Path, restored: dict[str, int]
) -> None:
    backed_up(store, tmp_path)

    with pytest.raises(DrillFailed) as failure:
        run_restore_drill(store, lambda home: FakeServer(home, restored), tmp_path)

    message = str(failure.value)
    assert message.startswith("The restored backup of 2026-10-07 differs from its manifest in ")
    assert ("uploads" in message) or ("stray" in message)


def test_without_any_backup_the_drill_fails(store: BackupStore, tmp_path: Path) -> None:
    with pytest.raises(NoBackup):
        run_restore_drill(store, lambda home: FakeServer(home, {}), tmp_path)


def test_the_drill_leaves_nothing_behind(store: BackupStore, tmp_path: Path) -> None:
    backed_up(store, tmp_path)

    run_restore_drill(store, lambda home: FakeServer(home, dict(TABLES)), tmp_path)

    assert list(tmp_path.iterdir()) == []
