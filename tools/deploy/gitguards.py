"""Guards that let only a clean, pushed `main` commit deploy."""

from __future__ import annotations

from tools.deploy.runner import CommandError, Runner


def head_sha(run: Runner) -> str:
    return run(["git", "rev-parse", "HEAD"]).stdout.strip()


def require_clean_tree(run: Runner) -> str:
    """The commit to operate on: the working tree must have no uncommitted changes. Works on
    any branch."""
    if run(["git", "status", "--porcelain"]).stdout.strip():
        raise CommandError("The working tree has uncommitted changes; commit or stash them first.")
    return head_sha(run)


def require_clean_main(run: Runner) -> str:
    """The commit to deploy: a clean `main` checkout equal to GitHub's `main`."""
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "main":
        raise CommandError(f"Deploys run from main; you're on '{branch}'. Run: git switch main && git pull --ff-only")
    local = require_clean_tree(run)
    run(["git", "fetch", "--quiet", "origin", "main"])
    remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    if local != remote:
        raise CommandError(f"Local main ({local[:7]}) differs from GitHub's main ({remote[:7]}). Run: git pull --ff-only")
    return local


def require_unchanged_since(run: Runner, sha: str, command: str) -> None:
    """Re-check the checkout right before Terraform touches anything, for either `plan` or
    `deploy`: preflight and the CI/CodeQL checks can take a while, and the owner (or something
    else) could edit or check out a different commit in the meantime."""
    if run(["git", "status", "--porcelain"]).stdout.strip():
        raise CommandError(
            f"The checkout changed during the {command} (uncommitted changes appeared); stopped before Terraform ran."
        )
    current = head_sha(run)
    if current != sha:
        raise CommandError(
            f"The checkout changed during the {command} (HEAD moved from {sha[:7]} to {current[:7]}); "
            "stopped before Terraform ran."
        )
