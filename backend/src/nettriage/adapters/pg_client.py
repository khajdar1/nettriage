"""Running the Postgres programs the `ops` function's layer holds (Plan 7a §2.3, §2.5). The
password travels in the program's environment, never on its command line; remote hosts get the
same verified TLS as the app's own connections; and a failure says only how the program exited,
never what it printed, which could quote data (spec §9.3)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import certifi
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

    def _run(
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

    def version(self) -> str:
        """The client's version, such as "17.11"."""
        return self._run("pg_dump", ["--version"], {}).stdout.split()[-1]

    def dump(self, target: Target, snapshot: str, out: Path) -> None:
        """A custom-format dump of the target's database as of an exported snapshot, reading
        through row-level security (the backup role's policies let it see every row)."""
        result = self._run(
            "pg_dump",
            [
                "--format=custom",
                "--enable-row-security",
                f"--snapshot={snapshot}",
                "--no-password",
                f"--file={out}",
            ],
            target.environment(),
        )
        if result.returncode != 0:
            raise DumpFailed(f"pg_dump exited with status {result.returncode}")
