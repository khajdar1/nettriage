import stat
import zipfile
from pathlib import Path

import pytest

from tools.build_lambda import PackageError, remove_console_scripts, validate_zip, write_zip


def make_package(
    root: Path,
    *,
    run_sh: bytes = b"#!/bin/sh\nexec true\n",
    wheel_tag: str = "py3-none-any",
    extra: dict[str, bytes] | None = None,
) -> Path:
    package = root / "package"
    files = {
        "run.sh": run_sh,
        "collector.yaml": b"receivers: {}\n",
        "nettriage/entrypoints/api/main.py": b"app = None\n",
        "nettriage/entrypoints/analyze/handler.py": b"def handle(event, context): pass\n",
        "fastapi-1.0.dist-info/WHEEL": f"Wheel-Version: 1.0\nTag: {wheel_tag}\n".encode(),
        **(extra or {}),
    }
    for name, content in files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return package


def build(tmp_path: Path, **kwargs: object) -> Path:
    out = tmp_path / "dist" / "backend.zip"
    write_zip(make_package(tmp_path, **kwargs), out)  # type: ignore[arg-type]
    return out


def test_valid_package_passes_and_run_sh_is_executable(tmp_path: Path) -> None:
    out = build(tmp_path)

    validate_zip(out)
    with zipfile.ZipFile(out) as zf:
        assert (zf.getinfo("run.sh").external_attr >> 16) & stat.S_IXUSR


def test_crlf_run_sh_is_rejected(tmp_path: Path) -> None:
    out = build(tmp_path, run_sh=b"#!/bin/sh\r\nexec true\r\n")

    with pytest.raises(PackageError, match="CRLF"):
        validate_zip(out)


@pytest.mark.parametrize(
    "tag",
    [
        "cp314-cp314-win_amd64",
        "cp314-cp314-manylinux_2_17_x86_64",
        "cp314-cp314-macosx_11_0_arm64",
    ],
)
def test_wrong_platform_wheels_are_rejected(tmp_path: Path, tag: str) -> None:
    out = build(tmp_path, wheel_tag=tag)

    with pytest.raises(PackageError):
        validate_zip(out)


def test_linux_arm64_wheels_are_accepted(tmp_path: Path) -> None:
    validate_zip(build(tmp_path, wheel_tag="cp314-cp314-manylinux_2_17_aarch64"))


def test_windows_binaries_are_rejected(tmp_path: Path) -> None:
    out = build(tmp_path, extra={"pydantic_core/_core.cp314-win_amd64.pyd": b"MZ"})

    with pytest.raises(PackageError, match="wrong-platform"):
        validate_zip(out)


def test_missing_entrypoint_is_rejected(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "nettriage/entrypoints/api/main.py").unlink()
    out = tmp_path / "backend.zip"
    write_zip(package, out)

    with pytest.raises(PackageError, match="missing nettriage/entrypoints/api/main.py"):
        validate_zip(out)


def test_a_package_without_the_analyze_handler_is_rejected(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "nettriage/entrypoints/analyze/handler.py").unlink()
    out = tmp_path / "backend.zip"
    write_zip(package, out)

    with pytest.raises(PackageError, match="missing nettriage/entrypoints/analyze/handler.py"):
        validate_zip(out)


def test_zip_is_reproducible(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"

    write_zip(package, first)
    write_zip(package, second)

    assert first.read_bytes() == second.read_bytes()


def test_exe_console_scripts_are_rejected(tmp_path: Path) -> None:
    out = build(tmp_path, extra={"bin/fastapi.exe": b"MZ"})

    with pytest.raises(PackageError, match="wrong-platform"):
        validate_zip(out)


def test_remove_console_scripts_drops_launchers_and_lock(tmp_path: Path) -> None:
    package = tmp_path / "package"
    files = {
        "bin/fastapi.exe": b"MZ",
        "bin/uvicorn.exe": b"MZ",
        "Scripts/idna.exe": b"MZ",
        ".lock": b"",
        "nettriage/entrypoints/api/main.py": b"app = None\n",
        "fastapi-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
    }
    for name, content in files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    remove_console_scripts(package)

    assert not (package / "bin").exists()
    assert not (package / "Scripts").exists()
    assert not (package / ".lock").exists()
    assert (package / "nettriage/entrypoints/api/main.py").read_bytes() == b"app = None\n"
    assert (package / "fastapi-1.0.dist-info/WHEEL").exists()


def test_a_zip_over_lambdas_upload_limit_is_rejected(tmp_path: Path) -> None:
    out = build(tmp_path)

    with pytest.raises(PackageError, match="Lambda takes at most 10"):
        validate_zip(out, max_zipped=10)


def test_a_package_over_lambdas_unzipped_limit_is_rejected(tmp_path: Path) -> None:
    out = build(tmp_path)

    with pytest.raises(PackageError, match="unzipped it is"):
        validate_zip(out, max_unzipped=10)
