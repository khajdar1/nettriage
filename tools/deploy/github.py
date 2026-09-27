"""A commit's CI runs, their artifacts and PR comments, through the GitHub CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tools.deploy.runner import CommandError, Runner


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    status: str
    conclusion: str
    event: str


def latest_run(run: Runner, workflow: str, sha: str, event: str | None = None) -> WorkflowRun | None:
    listing = run(
        ["gh", "run", "list", "--workflow", workflow, "--commit", sha,
         "--json", "databaseId,status,conclusion,event", "--limit", "20"]
    ).stdout
    found = [
        WorkflowRun(item["databaseId"], item["status"], item["conclusion"] or "", item["event"])
        for item in json.loads(listing)
    ]
    if event is not None:
        found = [item for item in found if item.event == event]
    return found[0] if found else None  # gh lists the newest run first


def require_success(run: Runner, workflow: str, sha: str, event: str) -> WorkflowRun:
    found = latest_run(run, workflow, sha, event)
    if found is None:
        raise CommandError(f"No {workflow} run found for {sha[:7]} ({event}). Push it and wait for CI.")
    if found.status != "completed":
        raise CommandError(f"{workflow} for {sha[:7]} is still {found.status}; wait for it to finish.")
    if found.conclusion != "success":
        raise CommandError(f"{workflow} for {sha[:7]} concluded '{found.conclusion}'; only green commits deploy.")
    return found


def download(run: Runner, run_id: int, name: str, dest: Path) -> Path:
    try:
        run(["gh", "run", "download", str(run_id), "--name", name, "--dir", str(dest)])
    except CommandError as exc:
        raise CommandError(
            f"Couldn't download '{name}' from run {run_id}: {exc} CI artifacts expire after 7 days: "
            "if they expired, re-run the workflow on GitHub, then try again."
        ) from exc
    return dest


def comment_on_pr(run: Runner, body_file: Path) -> None:
    try:
        run(["gh", "pr", "comment", "--body-file", str(body_file)])
    except CommandError as exc:
        raise CommandError(f"Couldn't comment on this branch's PR: {exc} If the branch has no open PR, open one, or plan with --no-comment.") from exc
