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
        redact: Sequence[str] = (),
    ) -> Result: ...


def run(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    interactive: bool = False,
    check: bool = True,
    redact: Sequence[str] = (),
) -> Result:
    """Run a command. Interactive commands share the terminal (terraform apply asks for "yes").

    Error messages name only the program and its first argument, never the rest, because some
    arguments are secrets (the SSM parameter value).
    """
    real_env = dict(env) if env is not None else None
    try:
        if interactive:
            process = subprocess.Popen(list(args), env=real_env, cwd=cwd)  # noqa: S603
            returncode, stderr = _wait_through_ctrl_c(process), ""
        else:
            completed = subprocess.run(  # noqa: S603 - argument list, no shell
                list(args),
                env=real_env,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            returncode, stderr = completed.returncode, completed.stderr
    except FileNotFoundError:
        raise CommandError(f"`{args[0]}` isn't installed or isn't on PATH.") from None
    if check and returncode != 0:
        tail = _redact_values(_last_line(stderr), redact)
        what = " ".join(args[:2])
        reason = f": {tail}" if tail else "."
        raise CommandError(f"`{what}` failed with exit code {returncode}{reason}")
    return Result(returncode, "" if interactive else completed.stdout)


def _wait_through_ctrl_c(process: subprocess.Popen) -> int:
    """Wait for an interactive child. It gets the same Ctrl+C we do, so let it stop on its own
    (e.g. Terraform saving state) instead of killing it."""
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


def _last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _redact_values(text: str, redact: Sequence[str]) -> str:
    """Replace non-empty redact values in text with ***."""
    result = text
    for value in redact:
        if value:
            result = result.replace(value, "***")
    return result
