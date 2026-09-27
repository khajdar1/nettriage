import pytest

from tools.deploy import session
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import SESSION, FakeRun


def test_session_credentials_and_stockholm_reach_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "someone-else")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIALONGLIVED")
    run = FakeRun().on("aws", "configure", "export-credentials", returns=SESSION)

    env = session.aws_env(run, "nettriage")

    assert run.calls[0].args == [
        "aws", "configure", "export-credentials", "--profile", "nettriage", "--format", "process",
    ]
    assert env["AWS_ACCESS_KEY_ID"] == "ASIAEXAMPLE"
    assert env["AWS_SESSION_TOKEN"] == "example-session"
    assert env["AWS_REGION"] == "eu-north-1"
    assert env["AWS_DEFAULT_REGION"] == "eu-north-1"
    assert "AWS_PROFILE" not in env


def test_long_lived_keys_are_refused() -> None:
    keys = '{"Version": 1, "AccessKeyId": "AKIA", "SecretAccessKey": "s"}'
    run = FakeRun().on("aws", "configure", "export-credentials", returns=keys)
    with pytest.raises(CommandError, match="long-lived"):
        session.aws_env(run, "nettriage")


def test_missing_session_says_how_to_sign_in() -> None:
    run = FakeRun().on("aws", "configure", "export-credentials", returns=CommandError("expired"))
    with pytest.raises(CommandError, match="aws login --profile nettriage"):
        session.aws_env(run, "nettriage")


def test_malformed_session_output_says_how_to_sign_in() -> None:
    run = FakeRun().on("aws", "configure", "export-credentials", returns="Note: a new version of the AWS CLI is available\n")
    with pytest.raises(CommandError, match="aws login --profile nettriage"):
        session.aws_env(run, "nettriage")


def test_incomplete_session_says_how_to_sign_in() -> None:
    run = FakeRun().on("aws", "configure", "export-credentials", returns='{"Version": 1, "SessionToken": "t"}')
    with pytest.raises(CommandError, match="aws login --profile nettriage"):
        session.aws_env(run, "nettriage")


def test_account_id_is_read_from_sts() -> None:
    run = FakeRun().on("aws", "sts", "get-caller-identity", returns="123456789012\n")
    assert session.account_id(run, {}) == "123456789012"
