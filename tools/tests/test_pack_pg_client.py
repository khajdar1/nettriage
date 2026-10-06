import stat
import zipfile
from pathlib import Path

import pytest

from tools.pack_pg_client import PackError, pack

BINARIES = ("initdb", "pg_ctl", "pg_dump", "pg_restore", "postgres")


def make_install(root: Path, *, skip: str | None = None) -> Path:
    """A tree shaped like `make install` with prefix /opt/pg (spec Plan 7a §4)."""
    install = root / "pg"
    files = {
        **{f"bin/{name}": f"#{name}".encode() for name in BINARIES},
        "bin/psql": b"#psql",
        "bin/pgbench": b"#pgbench",
        "lib/libpq.so.5.17": b"libpq",
        "lib/libpq.a": b"static",
        "lib/libecpg.so.6.17": b"ecpg",
        "lib/postgresql/plpgsql.so": b"plpgsql",
        "lib/postgresql/pgxs/src/makefiles/pgxs.mk": b"pgxs",
        "include/libpq-fe.h": b"header",
        "share/postgresql/postgres.bki": b"bki",
        "share/postgresql/timezone/UTC": b"tz",
    }
    for name, content in files.items():
        if name == skip:
            continue
        path = install / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return install


def entries(out: Path) -> dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(out) as zf:
        return {info.filename: info for info in zf.infolist()}


def test_the_layer_holds_only_what_the_ops_jobs_run(tmp_path: Path) -> None:
    out = tmp_path / "pg-client.zip"

    pack(make_install(tmp_path), out)

    assert sorted(entries(out)) == [
        "pg/bin/initdb",
        "pg/bin/pg_ctl",
        "pg/bin/pg_dump",
        "pg/bin/pg_restore",
        "pg/bin/postgres",
        "pg/lib/libpq.so.5",
        "pg/lib/postgresql/plpgsql.so",
        "pg/share/postgresql/postgres.bki",
        "pg/share/postgresql/timezone/UTC",
    ]


def test_libpq_is_stored_under_the_name_the_programs_load(tmp_path: Path) -> None:
    out = tmp_path / "pg-client.zip"

    pack(make_install(tmp_path), out)

    with zipfile.ZipFile(out) as zf:
        assert zf.read("pg/lib/libpq.so.5") == b"libpq"


def test_programs_and_libraries_are_executable_and_data_is_not(tmp_path: Path) -> None:
    out = tmp_path / "pg-client.zip"

    pack(make_install(tmp_path), out)

    modes = {name: stat.S_IMODE(info.external_attr >> 16) for name, info in entries(out).items()}
    assert modes["pg/bin/pg_dump"] == 0o755
    assert modes["pg/lib/libpq.so.5"] == 0o755
    assert modes["pg/share/postgresql/postgres.bki"] == 0o644


def test_the_same_install_always_packs_to_the_same_bytes(tmp_path: Path) -> None:
    install = make_install(tmp_path)
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"

    pack(install, first)
    pack(install, second)

    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize(
    "missing",
    ["bin/pg_dump", "bin/postgres", "lib/libpq.so.5.17", "share/postgresql/postgres.bki"],
)
def test_an_install_missing_a_needed_file_is_refused(tmp_path: Path, missing: str) -> None:
    with pytest.raises(PackError, match="missing"):
        pack(make_install(tmp_path, skip=missing), tmp_path / "pg-client.zip")
