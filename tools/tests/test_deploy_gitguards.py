import pytest

from tools.deploy import gitguards
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun

SHA = "b" * 40


def repo(branch: str = "main", status: str = "", local: str = SHA, remote: str = SHA) -> FakeRun:
    return (
        FakeRun()
        .on("git", "rev-parse", "--abbrev-ref", returns=f"{branch}\n")
        .on("git", "status", "--porcelain", returns=status)
        .on("git", "fetch")
        .on("git", "rev-parse", "HEAD", returns=f"{local}\n")
        .on("git", "rev-parse", "origin/main", returns=f"{remote}\n")
    )


def test_clean_main_matching_github_is_deployable() -> None:
    run = repo()
    assert gitguards.require_clean_main(run) == SHA
    assert run.called("git", "fetch")[0].args == ["git", "fetch", "--quiet", "origin", "main"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"branch": "plan-1/walking-skeleton"}, "you're on 'plan-1/walking-skeleton'"),
        ({"status": " M README.md\n"}, "uncommitted changes"),
        ({"remote": "c" * 40}, "differs from GitHub"),
    ],
)
def test_anything_else_is_refused(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(CommandError, match=message):
        gitguards.require_clean_main(repo(**kwargs))
