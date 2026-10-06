"""Running the layer's Postgres programs (Plan 7a §2.3): the password travels in the environment,
never on the command line, TLS is verified for remote hosts, and a failure says only how the
program exited, never what it printed (it could quote data)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import certifi
import pytest

from nettriage.adapters.pg_client import DumpFailed, PgClient, Target

NEON = "postgresql://app_backup:s3cret@ep-x.eu-central-1.aws.neon.tech/neondb"


class FakeRun:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.result = (returncode, stdout, stderr)

    def __call__(
        self, args: Sequence[str], *, env: dict[str, str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), env))
        returncode, stdout, stderr = self.result
        return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


def test_a_target_reads_its_connection_from_a_url() -> None:
    target = Target.from_url(NEON)

    assert (target.host, target.port, target.database, target.user, target.password) == (
        "ep-x.eu-central-1.aws.neon.tech",
        5432,
        "neondb",
        "app_backup",
        "s3cret",
    )


def test_the_dump_uses_the_snapshot_and_row_security_with_the_password_out_of_sight(
    tmp_path: Path,
) -> None:
    run = FakeRun()
    client = PgClient(Path("/opt/pg/bin"), run=run)

    client.dump(Target.from_url(NEON), "00000003-0000001B-1", tmp_path / "db.dump")

    [(args, env)] = run.calls
    assert args == [
        str(Path("/opt/pg/bin/pg_dump")),
        "--format=custom",
        "--enable-row-security",
        "--snapshot=00000003-0000001B-1",
        "--no-password",
        f"--file={tmp_path / 'db.dump'}",
    ]
    assert "s3cret" not in " ".join(args)
    assert {key: env[key] for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER")} == {
        "PGHOST": "ep-x.eu-central-1.aws.neon.tech",
        "PGPORT": "5432",
        "PGDATABASE": "neondb",
        "PGUSER": "app_backup",
    }
    assert env["PGPASSWORD"] == "s3cret"
    assert (env["PGSSLMODE"], env["PGSSLROOTCERT"]) == ("verify-full", certifi.where())
    assert env["PGCONNECT_TIMEOUT"] == "10"


def test_a_local_server_is_dumped_without_tls(tmp_path: Path) -> None:
    run = FakeRun()

    PgClient(Path("/opt/pg/bin"), run=run).dump(
        Target.from_url("postgresql://app_backup:pw@127.0.0.1:5432/nettriage"),
        "snap",
        tmp_path / "db.dump",
    )

    [(_, env)] = run.calls
    assert "PGSSLMODE" not in env


def test_a_failed_dump_says_only_how_it_exited(tmp_path: Path) -> None:
    run = FakeRun(returncode=1, stderr='pg_dump: error: query failed: ERROR: value "secret row"')

    with pytest.raises(DumpFailed) as failure:
        PgClient(Path("/opt/pg/bin"), run=run).dump(Target.from_url(NEON), "snap", tmp_path / "x")

    assert str(failure.value) == "pg_dump exited with status 1"
    assert "secret" not in str(failure.value)


def test_the_client_says_its_version() -> None:
    run = FakeRun(stdout="pg_dump (PostgreSQL) 17.11\n")

    assert PgClient(Path("/opt/pg/bin"), run=run).version() == "17.11"
