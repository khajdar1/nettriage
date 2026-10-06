"""The nightly backup's database and storage sides (Plan 7a §2.3, §2.5).

`exported_snapshot` opens a read-only REPEATABLE READ transaction as `app_backup`, exports its
snapshot and counts every table's rows in it; while it stays open, `pg_dump --snapshot` dumps
exactly the same moment, so the counts are what a restore must find. It also lists the roles the
row policies name, which a restore needs to exist first.

`BackupStore` keeps each night's dump and manifest under `pg/<day>.dump` and `pg/<day>.json`.
The manifest is written last, so a day counts as backed up only once both are there."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Connection, Engine, text

if TYPE_CHECKING:
    from types_boto3_s3.client import S3Client

PREFIX = "pg/"


@dataclass(frozen=True)
class Snapshot:
    id: str
    tables: dict[str, int]
    roles: list[str]
    server_version: str


@dataclass(frozen=True)
class Manifest:
    created_at: str
    server_version: str
    client_version: str
    tables: dict[str, int]
    roles: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, body: str | bytes) -> Manifest:
        return cls(**json.loads(body))


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _count_tables(connection: Connection) -> dict[str, int]:
    tables: list[str] = list(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
            )
        ).scalars()
    )
    return {
        table: int(
            connection.execute(text(f"SELECT count(*) FROM {_quoted(table)}")).scalar_one()  # noqa: S608
        )
        for table in tables
    }


def _policy_roles(connection: Connection) -> list[str]:
    """Only the roles a policy names: the others are only granted privileges, which a restore
    with `--no-privileges` doesn't recreate."""
    roles: list[str] = list(
        connection.execute(
            text("SELECT DISTINCT unnest(roles) FROM pg_policies WHERE schemaname = 'public'")
        ).scalars()
    )
    return sorted(role for role in roles if role != "public")


@contextmanager
def exported_snapshot(engine: Engine) -> Iterator[Snapshot]:
    with engine.connect() as connection:
        connection.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
        with connection.begin():
            snapshot_id = str(connection.execute(text("SELECT pg_export_snapshot()")).scalar_one())
            server_version = str(connection.execute(text("SHOW server_version")).scalar_one())
            yield Snapshot(
                id=snapshot_id,
                tables=_count_tables(connection),
                roles=_policy_roles(connection),
                server_version=server_version.split()[0],
            )


class BackupStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(self, day: str, dump: Path, manifest: Manifest) -> None:
        self.client.upload_file(str(dump), self.bucket, f"{PREFIX}{day}.dump")
        self.client.put_object(
            Bucket=self.bucket,
            Key=f"{PREFIX}{day}.json",
            Body=manifest.to_json().encode(),
            ContentType="application/json",
        )

    def latest(self) -> str | None:
        """The newest day with both its dump and its manifest."""
        names: set[str] = set()
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=PREFIX
        ):
            names.update(item["Key"].removeprefix(PREFIX) for item in page.get("Contents", []))
        days = {
            name.removesuffix(".dump")
            for name in names
            if name.endswith(".dump") and f"{name.removesuffix('.dump')}.json" in names
        }
        return max(days) if days else None

    def fetch(self, day: str, dump: Path) -> Manifest:
        self.client.download_file(self.bucket, f"{PREFIX}{day}.dump", str(dump))
        body = self.client.get_object(Bucket=self.bucket, Key=f"{PREFIX}{day}.json")["Body"]
        return Manifest.from_json(body.read())
