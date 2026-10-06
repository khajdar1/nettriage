"""Package the Postgres build from tools/pg_client/build.sh as the `ops` function's layer (Plan 7a).

Usage, from the repository root:
    python tools/pack_pg_client.py --src build/pg --out dist/pg-client.zip

A layer extracts to /opt, so every entry starts with `pg/`, the build's prefix (/opt/pg). Only
what the backup and the restore drill run is kept: five programs, libpq under the name they load
it by, the server's modules and its data files. The zip is deterministic, so an unchanged build
publishes no new layer version.
"""

from __future__ import annotations

import argparse
import stat
import sys
import zipfile
from pathlib import Path

PROGRAMS = ("initdb", "pg_ctl", "pg_dump", "pg_restore", "postgres")
LIBPQ = "libpq.so.5"
REQUIRED = (*(f"bin/{name}" for name in PROGRAMS), f"lib/{LIBPQ}", "share/postgresql/postgres.bki")
FIXED_DATE = (2020, 1, 1, 0, 0, 0)


class PackError(Exception):
    """The build is missing something the layer needs."""


def _libpq(lib: Path) -> Path | None:
    """The real file behind libpq's soname (libpq.so.5.17); the links to it aren't packed."""
    versions = sorted(lib.glob(f"{LIBPQ}.*"))
    return versions[-1] if versions else None


def _members(install: Path) -> dict[str, Path]:
    """Each kept file, by its path inside the layer's pg/ folder."""
    members: dict[str, Path] = {}
    for name in PROGRAMS:
        path = install / "bin" / name
        if path.is_file():
            members[f"bin/{name}"] = path
    libpq = _libpq(install / "lib")
    if libpq is not None:
        members[f"lib/{LIBPQ}"] = libpq
    for module in sorted((install / "lib" / "postgresql").glob("*.so")):
        members[f"lib/postgresql/{module.name}"] = module
    for path in sorted((install / "share").rglob("*")):
        if path.is_file():
            members[path.relative_to(install).as_posix()] = path
    return members


def pack(install: Path, out: Path) -> None:
    members = _members(install)
    missing = [name for name in REQUIRED if name not in members]
    if missing:
        raise PackError(f"the build is missing {', '.join(missing)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(f"pg/{name}", date_time=FIXED_DATE)
            mode = 0o755 if name.startswith(("bin/", "lib/")) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, members[name].read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True, help="the install prefix, e.g. build/pg")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        pack(args.src, args.out)
    except PackError as error:
        print(f"pack_pg_client: {error}", file=sys.stderr)
        return 1
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
