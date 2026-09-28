"""A local Postgres for tests, without Docker: `pgserver` bundles the server binaries.

Run with its own Python (pgserver has no Python 3.14 wheels):
    uv run --no-project --python 3.12 --with pgserver==0.1.4 python tools/localdb.py up|down

`up` starts the server in `.localdb/` (git-ignored), leaves it running, and writes its URL to
`.localdb/url` for `just test`. `down` stops it. CI uses a Postgres 17 service container
instead, through NETTRIAGE_TEST_DATABASE_URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pgserver  # type: ignore[import-not-found]

HOME = Path(__file__).resolve().parents[1] / ".localdb"
DATA = HOME / "data"
URL_FILE = HOME / "url"


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    if command == "up":
        HOME.mkdir(exist_ok=True)
        server = pgserver.get_server(DATA, cleanup_mode=None)
        URL_FILE.write_text(server.get_uri(), encoding="utf-8")
        print(f"Local Postgres is running: {server.get_uri()}")
        return 0
    if command == "down":
        if not DATA.exists():
            print("Local Postgres isn't set up.")
            return 0
        pgserver.get_server(DATA, cleanup_mode="stop")
        URL_FILE.unlink(missing_ok=True)
        print("Local Postgres stopped.")
        return 0
    print("usage: localdb.py up|down", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
