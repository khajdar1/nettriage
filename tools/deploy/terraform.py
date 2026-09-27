"""Terraform invocations shared by bootstrap, plan and deploy."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.deploy.config import REGION
from tools.deploy.runner import Runner


def backend_args(bucket: str, key: str) -> list[str]:
    return [
        f"-backend-config=bucket={bucket}",
        f"-backend-config=key={key}",
        f"-backend-config=region={REGION}",
        "-backend-config=use_lockfile=true",
    ]


def init(run: Runner, env: Mapping[str, str], cwd: Path, bucket: str, key: str) -> None:
    run(["terraform", "init", "-input=false", "-reconfigure", *backend_args(bucket, key)], env=env, cwd=cwd)


def planned_changes(plan_json: str) -> list[str]:
    """`<actions> <address>` lines for every real change: never attribute values."""
    lines = []
    for change in json.loads(plan_json).get("resource_changes", []):
        actions = change["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        lines.append(f"{'/'.join(actions)} {change['address']}")
    return lines


def plan(run: Runner, env: Mapping[str, str], cwd: Path, plan_file: Path) -> list[str]:
    run(["terraform", "plan", "-input=false", "-lock=false", f"-out={plan_file}"], env=env, cwd=cwd)
    return planned_changes(run(["terraform", "show", "-json", str(plan_file)], env=env, cwd=cwd).stdout)


def apply(run: Runner, env: Mapping[str, str], cwd: Path, extra_args: Sequence[str] = ()) -> None:
    """Show the plan in the terminal and let the owner confirm it with "yes"."""
    run(["terraform", "apply", "-input=true", *extra_args], env=env, cwd=cwd, interactive=True)


def outputs(run: Runner, env: Mapping[str, str], cwd: Path) -> dict[str, str]:
    data = json.loads(run(["terraform", "output", "-json"], env=env, cwd=cwd).stdout)
    return {name: str(item["value"]) for name, item in data.items()}
