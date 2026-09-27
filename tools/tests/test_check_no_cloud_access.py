from pathlib import Path

import pytest

from tools.check_no_cloud_access import TERRAFORM_RULES, WORKFLOW_RULES, main, problems_in

SHA = "a" * 40


def test_a_checks_only_workflow_is_clean() -> None:
    text = f"permissions:\n  contents: read\njobs:\n  b:\n    steps:\n      - uses: actions/checkout@{SHA}\n"
    assert problems_in(text, WORKFLOW_RULES) == []


def test_requesting_an_oidc_token_is_flagged() -> None:
    text = "    permissions:\n      id-token: write\n"
    assert problems_in(text, WORKFLOW_RULES) == ["requests an OIDC token (id-token: write)"]


def test_aws_actions_are_flagged() -> None:
    text = f"      - uses: aws-actions/configure-aws-credentials@{SHA}\n"
    assert "uses an AWS action" in problems_in(text, WORKFLOW_RULES)


def test_aws_credentials_are_flagged() -> None:
    text = "        env:\n          AWS_SECRET_ACCESS_KEY: ${{ secrets.KEY }}\n"
    assert problems_in(text, WORKFLOW_RULES) == ["references AWS credentials"]


def test_a_github_oidc_trust_in_terraform_is_flagged() -> None:
    text = (
        'resource "aws_iam_openid_connect_provider" "github" {\n'
        '  url = "https://token.actions.githubusercontent.com"\n}\n'
    )
    assert problems_in(text, TERRAFORM_RULES) == [
        "defines an OIDC identity provider",
        "trusts GitHub's OIDC issuer",
    ]


def _repo(root: Path, workflow: str, terraform: str) -> Path:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    (root / "infra" / "bootstrap").mkdir(parents=True)
    (root / "infra" / "bootstrap" / "main.tf").write_text(terraform, encoding="utf-8")
    return root


def test_main_passes_a_repo_without_cloud_access(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, "permissions:\n  contents: read\n", 'resource "aws_s3_bucket" "s" {}\n')
    assert main([str(root)]) == 0
    assert "CI holds no cloud access." in capsys.readouterr().out


def test_main_fails_and_names_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path, "permissions:\n  id-token: write\n", "")
    assert main([str(root)]) == 1
    assert "ci.yml: requests an OIDC token" in capsys.readouterr().out
