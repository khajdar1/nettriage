"""The nightly backup (Plan 7a §2.3): count every table in an exported snapshot, dump that same
snapshot, then store the dump and a manifest of the counts under the day's name. The local dump
is removed whatever happens, and a dump that fails stores nothing."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from nettriage.adapters.backups import BackupStore, Manifest, exported_snapshot
from nettriage.adapters.pg_client import Target

DUMP_NAME = "nettriage.dump"


class Dumper(Protocol):
    def version(self) -> str: ...

    def dump(self, target: Target, snapshot: str, out: Path) -> None: ...


def run_backup(
    engine: Engine, pg_dump: Dumper, store: BackupStore, now: datetime, workdir: Path
) -> Manifest:
    """`engine` connects as `app_backup` to the database's direct endpoint: `pg_dump` needs a
    session, which Neon's pooler doesn't keep."""
    dump = workdir / DUMP_NAME
    target = Target.from_url(engine.url.render_as_string(hide_password=False))
    try:
        with exported_snapshot(engine) as snapshot:
            pg_dump.dump(target, snapshot.id, dump)
        manifest = Manifest(
            created_at=now.isoformat(),
            server_version=snapshot.server_version,
            client_version=pg_dump.version(),
            tables=snapshot.tables,
            roles=snapshot.roles,
        )
        store.put(now.date().isoformat(), dump, manifest)
    finally:
        dump.unlink(missing_ok=True)
    return manifest
