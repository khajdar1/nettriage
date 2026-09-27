"""Terraform invocations shared by bootstrap, plan and deploy."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.deploy.config import REGION
from tools.deploy.runner import CommandError, Runner


def backend_args(bucket: str, key: str) -> list[str]:
    return [
        f"-backend-config=bucket={bucket}",
        f"-backend-config=key={key}",
        f"-backend-config=region={REGION}",
        "-backend-config=use_lockfile=true",
    ]


def init(run: Runner, env: Mapping[str, str], cwd: Path, bucket: str, key: str) -> None:
    # -lockfile=readonly: a deploy must never add hashes to the committed .terraform.lock.hcl.
    run(
        ["terraform", "init", "-input=false", "-no-color", "-reconfigure", "-lockfile=readonly",
         *backend_args(bucket, key)],
        env=env, cwd=cwd,
    )


def planned_changes(plan_output: str) -> list[str]:
    """`<action> <resource.addr>` lines for every real change, read from `terraform plan -json`'s
    newline-delimited "planned_change" messages: never attribute values, and no plan file."""
    lines = []
    for raw_line in plan_output.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        message = json.loads(raw_line)
        if message.get("type") != "planned_change":
            continue
        action = message["change"]["action"]
        if action in ("noop", "read"):
            continue
        lines.append(f"{action} {message['change']['resource']['addr']}")
    return lines


def _plan_error(plan_output: str) -> str:
    messages = []
    for raw_line in plan_output.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        message = json.loads(raw_line)
        if message.get("@level") == "error":
            messages.append(message.get("@message", ""))
    return "; ".join(message for message in messages if message)


def plan(run: Runner, env: Mapping[str, str], cwd: Path) -> list[str]:
    """No `-out` plan file: a saved plan can hold sensitive variable values on disk."""
    result = run(["terraform", "plan", "-input=false", "-lock=false", "-json"], env=env, cwd=cwd, check=False)
    if result.returncode != 0:
        raise CommandError(f"`terraform plan` failed: {_plan_error(result.stdout)}")
    return planned_changes(result.stdout)


def apply(run: Runner, env: Mapping[str, str], cwd: Path, extra_args: Sequence[str] = ()) -> None:
    """Show the plan in the terminal and let the owner confirm it with "yes"."""
    run(["terraform", "apply", "-input=true", *extra_args], env=env, cwd=cwd, interactive=True)


def outputs(run: Runner, env: Mapping[str, str], cwd: Path) -> dict[str, str]:
    data = json.loads(run(["terraform", "output", "-no-color", "-json"], env=env, cwd=cwd).stdout)
    return {name: str(item["value"]) for name, item in data.items()}
