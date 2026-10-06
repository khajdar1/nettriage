"""The nightly backup's database and storage sides (Plan 7a §2.3): a snapshot whose row counts the
dump will match, and the dump and its manifest in S3."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from conftest import REGION, Database
from sqlalchemy import text
from tenantdata import add_tenant

from nettriage.adapters.backups import BackupStore, Manifest, exported_snapshot
from nettriage.adapters.postgres import create_database_engine

BUCKET = "nettriage-test-backups"


def owner_counts(database: Database) -> dict[str, int]:
    with database.admin.begin() as connection:
        tables: list[str] = list(
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'"
                )
            ).scalars()
        )
        return {
            table: int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())  # noqa: S608
            for table in tables
        }


def test_the_snapshot_counts_every_row_of_every_table_as_the_backup_role(
    database: Database,
) -> None:
    add_tenant(database.admin)
    add_tenant(database.admin)

    with exported_snapshot(database.app_backup) as snapshot:
        assert snapshot.tables == owner_counts(database)
        assert snapshot.tables["organizations"] >= 2
        assert snapshot.server_version.startswith(("16.", "17."))


def test_the_snapshot_names_the_roles_the_policies_need(database: Database) -> None:
    with exported_snapshot(database.app_backup) as snapshot:
        assert {"app_triage", "app_ops", "app_backup"} <= set(snapshot.roles)
        assert "public" not in snapshot.roles


def test_the_dump_sees_the_same_moment_as_the_counts(database: Database) -> None:
    add_tenant(database.admin)
    with exported_snapshot(database.app_backup) as snapshot:
        before = snapshot.tables["organizations"]
        add_tenant(database.admin)  # written after the snapshot: the dump mustn't see it

        second = create_database_engine(
            database.app_backup.url.render_as_string(hide_password=False), pool_size=1
        )
        with second.connect() as other:
            other.execution_options(isolation_level="REPEATABLE READ")
            with other.begin():
                other.execute(text(f"SET TRANSACTION SNAPSHOT '{snapshot.id}'"))
                seen: int = other.execute(text("SELECT count(*) FROM organizations")).scalar_one()
        second.dispose()

    assert seen == before


@pytest.fixture
def store() -> Iterator[BackupStore]:
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-north-1"}
        )
        yield BackupStore(client, BUCKET)


def manifest(day: str) -> Manifest:
    return Manifest(
        created_at=f"{day}T02:00:03+00:00",
        server_version="17.5",
        client_version="17.11",
        tables={"organizations": 2, "uploads": 5},
        roles=["app_api", "app_backup"],
    )


def test_a_backup_is_stored_as_its_day_dump_and_manifest(
    store: BackupStore, tmp_path: Path
) -> None:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP custom archive")

    store.put("2026-10-07", dump, manifest("2026-10-07"))

    objects = store.client.list_objects_v2(Bucket=BUCKET)["Contents"]
    assert sorted(item["Key"] for item in objects) == ["pg/2026-10-07.dump", "pg/2026-10-07.json"]
    body = store.client.get_object(Bucket=BUCKET, Key="pg/2026-10-07.json")["Body"].read()
    assert json.loads(body)["tables"] == {"organizations": 2, "uploads": 5}


def test_the_latest_backup_is_the_newest_day_with_both_files(
    store: BackupStore, tmp_path: Path
) -> None:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP")
    for day in ("2026-10-05", "2026-10-07", "2026-10-06"):
        store.put(day, dump, manifest(day))
    store.client.put_object(Bucket=BUCKET, Key="pg/2026-10-08.dump", Body=b"no manifest yet")

    assert store.latest() == "2026-10-07"


def test_no_backup_yet_has_no_latest(store: BackupStore) -> None:
    assert store.latest() is None


def test_a_backup_is_fetched_with_its_manifest(store: BackupStore, tmp_path: Path) -> None:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP archive bytes")
    store.put("2026-10-07", dump, manifest("2026-10-07"))
    fetched = tmp_path / "fetched.dump"

    got = store.fetch("2026-10-07", fetched)

    assert got == manifest("2026-10-07")
    assert fetched.read_bytes() == b"PGDMP archive bytes"
