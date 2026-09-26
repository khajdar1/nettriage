"""Build and validate the Lambda deployment package (arm64, python3.14).

Usage, from the repository root:  python tools/build_lambda.py --out dist/backend.zip
"""

from __future__ import annotations

import argparse
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
PLATFORM = "aarch64-manylinux_2_28"
PYTHON_VERSION = "3.14"
EXECUTABLES = frozenset({"run.sh"})
REQUIRED = ("run.sh", "collector.yaml", "nettriage/entrypoints/api/main.py")
ALLOWED_WHEEL_TAG = re.compile(
    r"^Tag: \S+-\S+-(any|(manylinux|musllinux)\S*_aarch64|linux_aarch64)$"
)
FORBIDDEN_NAME = re.compile(r"(win_amd64|win32|macosx|x86_64|\.pyd$|\.dll$|\.dylib$|\.exe$)")
# uv/pip write native console-script launchers for entry points (e.g. bin/fastapi.exe)
# based on the *build host's* platform, not --python-platform. On a Windows host these
# are Windows PE binaries; Lambda's run.sh never invokes them (it calls `python -m
# uvicorn ...` directly), so they are dropped rather than shipped and merely rejected.
GENERATED_SCRIPT_DIRS = ("bin", "Scripts")
FIXED_DATE = (2020, 1, 1, 0, 0, 0)


class PackageError(Exception):
    """The package would not run on Lambda."""


def remove_console_scripts(package: Path) -> None:
    """Drop uv/pip's console-script launchers, written for the build host's platform
    (not --python-platform), and the install .lock file; run.sh never invokes them."""
    for junk in GENERATED_SCRIPT_DIRS:
        shutil.rmtree(package / junk, ignore_errors=True)
    (package / ".lock").unlink(missing_ok=True)


def stage_package(build_dir: Path) -> Path:
    """Install Lambda-platform wheels and copy the app into build_dir/package."""
    if build_dir.exists():
        shutil.rmtree(build_dir)
    package = build_dir / "package"
    package.mkdir(parents=True)
    requirements = build_dir / "requirements.txt"
    subprocess.run(
        ["uv", "export", "--project", str(BACKEND), "--frozen", "--no-dev", "--no-hashes",
         "--no-emit-project", "--output-file", str(requirements)],
        check=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--target", str(package), "--python-platform", PLATFORM,
         "--python-version", PYTHON_VERSION, "--only-binary", ":all:",
         "--requirement", str(requirements)],
        check=True,
    )
    remove_console_scripts(package)
    shutil.copytree(
        BACKEND / "src" / "nettriage",
        package / "nettriage",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("run.sh", "collector.yaml"):
        shutil.copy2(BACKEND / "lambda" / name, package / name)
    return package


def write_zip(package: Path, out: Path) -> None:
    """Zip deterministically: fixed timestamps, sorted entries, explicit file modes."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in package.rglob("*") if p.is_file()):
            arcname = path.relative_to(package).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE)
            mode = 0o755 if arcname in EXECUTABLES else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())


def validate_zip(out: Path) -> None:
    problems: list[str] = []
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        problems += [f"missing {name}" for name in REQUIRED if name not in names]
        if "run.sh" in names:
            if not (zf.getinfo("run.sh").external_attr >> 16) & 0o111:
                problems.append("run.sh is not executable")
            if b"\r" in zf.read("run.sh"):
                problems.append("run.sh has CRLF line endings")
        for name in sorted(names):
            if FORBIDDEN_NAME.search(name):
                problems.append(f"wrong-platform file {name}")
            if name.endswith(".dist-info/WHEEL"):
                for line in zf.read(name).decode().splitlines():
                    if line.startswith("Tag: ") and not ALLOWED_WHEEL_TAG.match(line):
                        problems.append(f"wrong-platform wheel {name}: {line}")
    if problems:
        raise PackageError("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "dist" / "backend.zip")
    args = parser.parse_args(argv)
    package = stage_package(REPO / "build" / "lambda")
    write_zip(package, args.out)
    validate_zip(args.out)
    print(f"built {args.out} ({args.out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
