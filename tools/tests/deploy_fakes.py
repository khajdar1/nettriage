"""A scripted stand-in for tools.deploy.runner.run, shared by the deploy tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.deploy.runner import CommandError, Result


@dataclass
class Call:
    args: list[str]
    env: Mapping[str, str] | None
    cwd: Path | None
    interactive: bool
    redact: tuple[str, ...]


# A rule's answer: stdout text; an exit code (for check=False probes); an exception to raise;
# or a function of the call that returns stdout, or a full Result for a non-zero exit code with
# its own stdout (e.g. `terraform plan -json`'s diagnostics), and may create files or raise.
Answer = str | int | Exception | Callable[[Call], "str | Result"]


@dataclass
class FakeRun:
    """Answers each command with the first rule whose prefix matches, and records every call."""

    rules: list[tuple[tuple[str, ...], Answer]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def on(self, *prefix: str, returns: Answer = "") -> FakeRun:
        self.rules.append((prefix, returns))
        return self

    def __call__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        interactive: bool = False,
        check: bool = True,
        redact: Sequence[str] = (),
    ) -> Result:
        call = Call(list(args), env, cwd, interactive, tuple(redact))
        self.calls.append(call)
        for prefix, answer in self.rules:
            if tuple(call.args[: len(prefix)]) != prefix:
                continue
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, int):
                if check and answer != 0:
                    raise CommandError(f"`{' '.join(call.args[:2])}` failed with exit code {answer}.")
                return Result(answer, "")
            if callable(answer):
                outcome = answer(call)
                return outcome if isinstance(outcome, Result) else Result(0, outcome)
            return Result(0, answer)
        raise AssertionError(f"unexpected command: {call.args}")

    def called(self, *prefix: str) -> list[Call]:
        return [call for call in self.calls if tuple(call.args[: len(prefix)]) == prefix]

    def first(self, *prefix: str) -> int:
        """Index of the first call starting with prefix, for ordering assertions."""
        return next(
            i for i, call in enumerate(self.calls) if tuple(call.args[: len(prefix)]) == prefix
        )


SESSION = json.dumps(
    {
        "Version": 1,
        "AccessKeyId": "ASIAEXAMPLE",
        "SecretAccessKey": "example-secret",
        "SessionToken": "example-session",
        "Expiration": "2026-09-27T20:00:00Z",
    }
)


TOOLS_CREDENTIAL_PROCESS = "aws configure export-credentials --profile nettriage --format process"


def signed_in() -> FakeRun:
    """A runner whose owner is signed in to account 123456789012, with the nettriage-tools
    helper profile already set up so aws_env's `aws configure get` check succeeds without change."""
    return (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=f"{TOOLS_CREDENTIAL_PROCESS}\n")
        .on("aws", "sts", "get-caller-identity", returns="123456789012\n")
    )


def runs(*items: tuple[int, str, str, str]) -> str:
    """`gh run list --json databaseId,status,conclusion,event` output, newest first."""
    return json.dumps(
        [{"databaseId": i, "status": s, "conclusion": c, "event": e} for i, s, c, e in items]
    )
