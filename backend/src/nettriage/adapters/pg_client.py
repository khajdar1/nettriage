"""Running the Postgres programs the `ops` function's layer holds (Plan 7a §2.3, §2.5). The
password travels in the program's environment, never on its command line; remote hosts get the
same verified TLS as the app's own connections; and a failure says only how the program exited,
never what it printed, which could quote data (spec §9.3)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import certifi
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url

from nettriage.adapters.postgres import CONNECT_TIMEOUT_SECONDS, LOCAL_HOSTS

# Where Lambda extracts the layer (tools/pack_pg_client.py).
LAYER_BIN = Path("/opt/pg/bin")

type Run = Callable[..., subprocess.CompletedProcess[str]]


class ProgramFailed(Exception):
    """A Postgres program exited with an error; the message holds its status only."""


class DumpFailed(ProgramFailed):
    pass


@dataclass(frozen=True)
class Target:
    """Where a program connects, and as whom."""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_url(cls, url: str) -> Target:
        parsed = make_url(url)
        return cls(
            host=parsed.host or "",
            port=parsed.port or 5432,
            database=parsed.database or "",
            user=parsed.username or "",
            password=parsed.password or "",
        )

    def environment(self) -> dict[str, str]:
        env = {
            "PGHOST": self.host,
            "PGPORT": str(self.port),
            "PGDATABASE": self.database,
            "PGUSER": self.user,
            "PGPASSWORD": self.password,
            "PGCONNECT_TIMEOUT": str(CONNECT_TIMEOUT_SECONDS),
        }
        if self.host not in LOCAL_HOSTS:
            env |= {"PGSSLMODE": "verify-full", "PGSSLROOTCERT": certifi.where()}
        return env


class PgClient:
    def __init__(self, bin_dir: Path = LAYER_BIN, run: Run = subprocess.run) -> None:
        self.bin_dir = bin_dir
        self.run = run

    def program(
        self, program: str, args: Sequence[str], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        # Only what the program needs: nothing else from this function's environment.
        return self.run(
            [str(self.bin_dir / program), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def checked(
        self,
        program: str,
        args: Sequence[str],
        env: dict[str, str],
        failure: type[ProgramFailed] = ProgramFailed,
    ) -> None:
        result = self.program(program, args, env)
        if result.returncode != 0:
            raise failure(f"{program} exited with status {result.returncode}")

    def version(self) -> str:
        """The client's version, such as "17.11"."""
        return self.program("pg_dump", ["--version"], {}).stdout.split()[-1]

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        """A custom-format dump of the target's database as of an exported snapshot, reading
        through row-level security (the backup role's policies let it see every row)."""
        self.checked(
            "pg_dump",
            [
                "--format=custom",
                "--enable-row-security",
                f"--snapshot={snapshot}",
                "--no-password",
                f"--file={out}",
            ],
            target.environment(),
            DumpFailed,
        )


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SqlRunner(Protocol):
    def execute(self, conninfo: str, statements: list[str]) -> None: ...

    def count_tables(self, conninfo: str) -> dict[str, int]: ...


class PsycopgSql:
    """The drill's SQL, through psycopg (which brings its own libpq)."""

    def execute(self, conninfo: str, statements: list[str]) -> None:
        """Outside a transaction, as CREATE DATABASE needs. The statements are built by the
        drill from quoted identifiers, never from input."""
        with psycopg.connect(conninfo, autocommit=True) as connection:
            for statement in statements:
                connection.execute(statement.encode())

    def count_tables(self, conninfo: str) -> dict[str, int]:
        with psycopg.connect(conninfo) as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
                )
            ]
            counts: dict[str, int] = {}
            for table in tables:
                row = connection.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone()
                counts[table] = int(row[0]) if row else 0
            return counts


class RestoreFailed(ProgramFailed):
    pass


# The throwaway server's names (Plan 7a §2.5).
SUPERUSER = "drill"
RESTORER = "restorer"
RESTORED = "restored"
PORT = 5433


class ThrowawayServer:
    """A Postgres server in its own folder for one restore drill, reachable only on a Unix
    socket there, with `mmap` shared memory (Lambda has no /dev/shm) and no durability (it's
    thrown away). The restore runs as a non-superuser owner, as a recovery into Neon would; the
    rows are counted as the server's superuser, past row-level security. Leaving the `with`
    block stops the server, whatever happened."""

    def __init__(self, client: PgClient, home: Path, sql: SqlRunner | None = None) -> None:
        self.client = client
        self.home = home
        self.data = home / "data"
        self.sql: SqlRunner = sql or PsycopgSql()
        self.started = False

    def __enter__(self) -> ThrowawayServer:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.started:
            # Best effort: a stop that fails mustn't hide what failed the drill.
            self.client.program("pg_ctl", [f"--pgdata={self.data}", "-m", "fast", "-w", "stop"], {})
            self.started = False

    def _conninfo(self, user: str, database: str) -> str:
        return make_conninfo(host=str(self.home), port=str(PORT), user=user, dbname=database)

    def prepare(self, roles: list[str]) -> None:
        """Initialize and start the server, then create the dump's policy roles and the owner
        the restore runs as."""
        self.home.mkdir(parents=True, exist_ok=True)
        self.client.checked(
            "initdb",
            [
                f"--pgdata={self.data}",
                f"--username={SUPERUSER}",
                "--auth=trust",
                "--no-sync",
                "--encoding=UTF8",
                "--locale=C",
            ],
            {},
        )
        options = " ".join(
            [
                "-c dynamic_shared_memory_type=mmap",
                "-c listen_addresses=",
                f"-c unix_socket_directories={self.home}",
                f"-c port={PORT}",
                "-c fsync=off",
                "-c full_page_writes=off",
                "-c synchronous_commit=off",
            ]
        )
        self.client.checked(
            "pg_ctl",
            [
                f"--pgdata={self.data}",
                f"--log={self.home / 'server.log'}",
                "-w",
                "-o",
                options,
                "start",
            ],
            {},
        )
        self.started = True
        self.sql.execute(
            self._conninfo(SUPERUSER, "postgres"),
            [f"CREATE ROLE {quote_identifier(role)} NOLOGIN" for role in roles]
            + [
                f"CREATE ROLE {quote_identifier(RESTORER)} LOGIN NOSUPERUSER",
                f"CREATE DATABASE {quote_identifier(RESTORED)} OWNER {quote_identifier(RESTORER)}",
            ],
        )

    def restore(self, dump: Path) -> None:
        self.client.checked(
            "pg_restore",
            [f"--dbname={RESTORED}", "--no-owner", "--no-privileges", "--exit-on-error", str(dump)],
            {"PGHOST": str(self.home), "PGPORT": str(PORT), "PGUSER": RESTORER},
            RestoreFailed,
        )

    def counts(self) -> dict[str, int]:
        return self.sql.count_tables(self._conninfo(SUPERUSER, RESTORED))
