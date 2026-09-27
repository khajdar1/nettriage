"""The one place that starts external commands (aws, gh, git, terraform), so tests can swap in a fake."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CommandError(Exception):
    """A step failed. The message says what happened and what to do next; it never contains secrets."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str


class Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        interactive: bool = False,
        check: bool = True,
    ) -> Result: ...


def run(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    interactive: bool = False,
    check: bool = True,
) -> Result:
    """Run a command. Interactive commands share the terminal (terraform apply asks for "yes").

    Error messages name only the program and its first argument, never the rest, because some
    arguments are secrets (the SSM parameter value).
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argument list, no shell
            list(args),
            env=dict(env) if env is not None else None,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            stdout=None if interactive else subprocess.PIPE,
            stderr=None if interactive else subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        raise CommandError(f"`{args[0]}` isn't installed or isn't on PATH.") from None
    if check and completed.returncode != 0:
        tail = "" if interactive else _last_line(completed.stderr)
        what = " ".join(args[:2])
        reason = f": {tail}" if tail else "."
        raise CommandError(f"`{what}` failed with exit code {completed.returncode}{reason}")
    return Result(completed.returncode, "" if interactive else completed.stdout)


def _last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
