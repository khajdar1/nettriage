from pathlib import Path

import pytest

from tools.deploy import github
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun, runs

SHA = "a" * 40


def test_latest_run_filters_by_event_newest_first() -> None:
    run = FakeRun().on(
        "gh", "run", "list",
        returns=runs((3, "completed", "success", "pull_request"), (2, "completed", "success", "push")),
    )
    found = github.latest_run(run, "ci.yml", SHA, event="push")
    assert found is not None and found.run_id == 2
    args = run.calls[0].args
    assert args[:5] == ["gh", "run", "list", "--workflow", "ci.yml"]
    assert args[args.index("--commit") + 1] == SHA


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        (runs(), "No ci.yml run"),
        (runs((1, "in_progress", "", "push")), "still in_progress"),
        (runs((1, "completed", "failure", "push")), "concluded 'failure'"),
    ],
)
def test_only_green_commits_pass(listing: str, message: str) -> None:
    run = FakeRun().on("gh", "run", "list", returns=listing)
    with pytest.raises(CommandError, match=message):
        github.require_success(run, "ci.yml", SHA, event="push")


def test_green_run_is_returned() -> None:
    run = FakeRun().on("gh", "run", "list", returns=runs((7, "completed", "success", "push")))
    assert github.require_success(run, "ci.yml", SHA, event="push").run_id == 7


def test_failed_run_message_says_planned_or_deployed() -> None:
    run = FakeRun().on("gh", "run", "list", returns=runs((1, "completed", "failure", "push")))
    with pytest.raises(CommandError, match="only green commits are planned or deployed"):
        github.require_success(run, "ci.yml", SHA, event="push")


def test_require_success_without_an_event_accepts_any_event() -> None:
    """`plan` checks a commit's CI run regardless of whether it ran for a `pull_request` or a
    `push` (R17): no `event` means `latest_run` doesn't filter by event at all."""
    run = FakeRun().on(
        "gh", "run", "list", returns=runs((7, "completed", "success", "pull_request"))
    )
    assert github.require_success(run, "ci.yml", SHA).run_id == 7


def test_require_success_without_an_event_omits_it_from_the_missing_run_message() -> None:
    run = FakeRun().on("gh", "run", "list", returns=runs())
    message = r"No ci\.yml run found for aaaaaaa\. Push it and wait for CI\.$"
    with pytest.raises(CommandError, match=message):
        github.require_success(run, "ci.yml", SHA)


def test_expired_artifacts_are_explained(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "run", "download", returns=CommandError("no artifact matches"))
    with pytest.raises(CommandError, match="expire after 7 days"):
        github.download(run, 7, "backend-zip", tmp_path)


def test_download_names_the_run_artifact_and_directory(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "run", "download")
    assert github.download(run, 7, "web-dist", tmp_path / "web") == tmp_path / "web"
    assert run.calls[0].args == [
        "gh", "run", "download", "7", "--name", "web-dist", "--dir", str(tmp_path / "web"),
    ]


def test_a_branch_without_a_pr_is_explained(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "pr", "comment", returns=CommandError("no pull requests found"))
    with pytest.raises(CommandError, match="--no-comment"):
        github.comment_on_pr(run, tmp_path / "plan.md")


def test_download_failures_keep_their_cause(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "run", "download", returns=CommandError("`gh` isn't installed or isn't on PATH."))
    with pytest.raises(CommandError) as exc_info:
        github.download(run, 7, "backend-zip", tmp_path)
    msg = str(exc_info.value)
    assert "isn't installed" in msg
    assert "expire after 7 days" in msg


def test_comment_failures_keep_their_cause(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "pr", "comment", returns=CommandError("HTTP 401: Bad credentials"))
    with pytest.raises(CommandError) as exc_info:
        github.comment_on_pr(run, tmp_path / "plan.md")
    msg = str(exc_info.value)
    assert "401" in msg
    assert "--no-comment" in msg
