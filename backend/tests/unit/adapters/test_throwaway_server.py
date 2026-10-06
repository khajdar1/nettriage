"""The restore drill's throwaway server (Plan 7a §2.5): initialized and started in its own
folder, reachable only on a Unix socket, with `mmap` shared memory (Lambda has no /dev/shm); the
dump's policy roles and a non-superuser owner exist before the restore, which runs as that
owner, as a real recovery into Neon would; and the server stops whatever happens."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from nettriage.adapters.pg_client import PgClient, ProgramFailed, RestoreFailed, ThrowawayServer

BIN = Path("/opt/pg/bin")


class FakeRun:
    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(
        self, args: Sequence[str], *, env: dict[str, str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        program = Path(args[0]).name
        self.calls.append((program, list(args[1:]), env))
        returncode = 1 if program == self.fail else 0
        return subprocess.CompletedProcess(list(args), returncode, "", "secret row in stderr")

    def programs(self) -> list[str]:
        return [program for program, _, _ in self.calls]


class FakeSql:
    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self.statements: list[tuple[str, str]] = []
        self.counts = counts or {}

    def execute(self, conninfo: str, statements: list[str]) -> None:
        self.statements.extend((conninfo, statement) for statement in statements)

    def count_tables(self, conninfo: str) -> dict[str, int]:
        self.statements.append((conninfo, "<count>"))
        return self.counts


def server(tmp_path: Path, run: FakeRun, sql: FakeSql) -> ThrowawayServer:
    return ThrowawayServer(PgClient(BIN, run=run), tmp_path / "drill", sql=sql)


def test_the_server_starts_in_its_folder_on_a_socket_only(tmp_path: Path) -> None:
    run, sql = FakeRun(), FakeSql()

    with server(tmp_path, run, sql) as drill:
        drill.prepare(["app_ops", "app_backup"])

    initdb, start = run.calls[0], run.calls[1]
    data = str(tmp_path / "drill" / "data")
    assert initdb[0] == "initdb"
    assert initdb[1] == [
        f"--pgdata={data}",
        "--username=drill",
        "--auth=trust",
        "--no-sync",
        "--encoding=UTF8",
        "--locale=C",
    ]
    assert start[0] == "pg_ctl"
    options = start[1][start[1].index("-o") + 1]
    assert "-c dynamic_shared_memory_type=mmap" in options
    assert "-c listen_addresses=" in options
    assert f"-c unix_socket_directories={tmp_path / 'drill'}" in options
    assert "-c fsync=off" in options
    assert start[1][-1] == "start"
    assert "-w" in start[1]


def test_the_policy_roles_and_a_non_superuser_owner_exist_before_the_restore(
    tmp_path: Path,
) -> None:
    run, sql = FakeRun(), FakeSql()

    with server(tmp_path, run, sql) as drill:
        drill.prepare(["app_ops", 'we"ird'])

    assert [statement for _, statement in sql.statements] == [
        'CREATE ROLE "app_ops" NOLOGIN',
        'CREATE ROLE "we""ird" NOLOGIN',
        'CREATE ROLE "restorer" LOGIN NOSUPERUSER',
        'CREATE DATABASE "restored" OWNER "restorer"',
    ]
    conninfo = sql.statements[0][0]
    assert {key: conninfo_to_dict(conninfo)[key] for key in ("host", "user")} == {
        "host": str(tmp_path / "drill"),
        "user": "drill",
    }


def test_the_dump_is_restored_as_the_owner_without_owners_or_privileges(tmp_path: Path) -> None:
    run, sql = FakeRun(), FakeSql()
    dump = tmp_path / "latest.dump"

    with server(tmp_path, run, sql) as drill:
        drill.prepare([])
        drill.restore(dump)

    [(_, args, env)] = [call for call in run.calls if call[0] == "pg_restore"]
    assert args == [
        "--dbname=restored",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        str(dump),
    ]
    assert (env["PGHOST"], env["PGUSER"]) == (str(tmp_path / "drill"), "restorer")
    assert "PGPASSWORD" not in env


def test_the_restored_rows_are_counted_as_the_servers_superuser(tmp_path: Path) -> None:
    run, sql = FakeRun(), FakeSql(counts={"organizations": 3})

    with server(tmp_path, run, sql) as drill:
        drill.prepare([])
        assert drill.counts() == {"organizations": 3}

    conninfo = conninfo_to_dict(sql.statements[-1][0])
    assert (conninfo["user"], conninfo["dbname"]) == ("drill", "restored")


def drill(throwaway: ThrowawayServer, dump: Path) -> None:
    with throwaway as running:
        running.prepare([])
        running.restore(dump)


def test_a_failed_restore_says_only_how_it_exited_and_still_stops_the_server(
    tmp_path: Path,
) -> None:
    run, sql = FakeRun(fail="pg_restore"), FakeSql()

    with pytest.raises(RestoreFailed) as failure:
        drill(server(tmp_path, run, sql), tmp_path / "latest.dump")

    assert str(failure.value) == "pg_restore exited with status 1"
    assert run.programs()[-1] == "pg_ctl"
    assert run.calls[-1][1][-1] == "stop"


def test_a_server_that_wont_initialize_fails_the_drill(tmp_path: Path) -> None:
    run, sql = FakeRun(fail="initdb"), FakeSql()

    with pytest.raises(ProgramFailed, match="initdb exited with status 1"):
        drill(server(tmp_path, run, sql), tmp_path / "latest.dump")

    assert sql.statements == []
    assert run.programs() == ["initdb"]
