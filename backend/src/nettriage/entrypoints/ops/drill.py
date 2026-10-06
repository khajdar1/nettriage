"""The weekly restore drill (Plan 7a §2.5): restore the newest backup into a throwaway server
and check every table's rows against its manifest. A difference fails the drill, naming the
tables (never their rows); the dump and the server's folder are removed whatever happens."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from nettriage.adapters.backups import BackupStore


class NoBackup(Exception):
    pass


class DrillFailed(Exception):
    pass


@dataclass(frozen=True)
class DrillResult:
    day: str
    tables: int
    rows: int


class Server(Protocol):
    def __enter__(self) -> Server: ...

    def __exit__(self, *exc: object) -> None: ...

    def prepare(self, roles: list[str]) -> None: ...

    def restore(self, dump: Path) -> None: ...

    def counts(self) -> dict[str, int]: ...


def run_restore_drill(
    store: BackupStore, throwaway: Callable[[Path], Server], workdir: Path
) -> DrillResult:
    day = store.latest()
    if day is None:
        raise NoBackup("There's no backup to restore yet.")
    dump = workdir / "latest.dump"
    home = workdir / "drill"
    try:
        manifest = store.fetch(day, dump)
        with throwaway(home) as server:
            server.prepare(manifest.roles)
            server.restore(dump)
            restored = server.counts()
    finally:
        dump.unlink(missing_ok=True)
        shutil.rmtree(home, ignore_errors=True)
    differing = sorted(
        table
        for table in manifest.tables.keys() | restored.keys()
        if manifest.tables.get(table) != restored.get(table)
    )
    if differing:
        raise DrillFailed(
            f"The restored backup of {day} differs from its manifest in {', '.join(differing)}."
        )
    return DrillResult(day=day, tables=len(manifest.tables), rows=sum(manifest.tables.values()))
