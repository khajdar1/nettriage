import pytest

from tools.deploy import session
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import SESSION, TOOLS_CREDENTIAL_PROCESS, FakeRun


def test_session_credentials_and_stockholm_reach_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "someone-else")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIALONGLIVED")
    run = (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=f"{TOOLS_CREDENTIAL_PROCESS}\n")
    )

    env = session.aws_env(run, "nettriage")

    assert run.calls[0].args == [
        "aws", "configure", "export-credentials", "--profile", "nettriage", "--format", "process",
    ]
    assert env["AWS_PROFILE"] == "nettriage-tools"
    assert env["AWS_REGION"] == "eu-north-1"
    assert env["AWS_DEFAULT_REGION"] == "eu-north-1"
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "AWS_SESSION_TOKEN" not in env


def test_a_missing_tools_profile_is_created(capsys: pytest.CaptureFixture[str]) -> None:
    run = (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=1)
        .on("aws", "configure", "set")
    )

    session.aws_env(run, "nettriage")

    [set_process] = run.called("aws", "configure", "set", "credential_process")
    assert set_process.args == [
        "aws", "configure", "set", "credential_process", TOOLS_CREDENTIAL_PROCESS,
        "--profile", "nettriage-tools",
    ]
    [set_region] = run.called("aws", "configure", "set", "region")
    assert set_region.args == [
        "aws", "configure", "set", "region", "eu-north-1", "--profile", "nettriage-tools",
    ]
    assert "Created AWS profile 'nettriage-tools'" in capsys.readouterr().out


def test_a_different_tools_profile_is_replaced() -> None:
    run = (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns="something-else\n")
        .on("aws", "configure", "set")
    )

    session.aws_env(run, "nettriage")

    assert len(run.called("aws", "configure", "set")) == 2


def test_an_existing_correct_tools_profile_is_untouched() -> None:
    run = (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=f"{TOOLS_CREDENTIAL_PROCESS}\n")
    )

    session.aws_env(run, "nettriage")

    assert run.called("aws", "configure", "set") == []


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
    with pytest.raises(CommandError, match="No usable AWS session"):
        session.aws_env(run, "nettriage")


def test_incomplete_session_says_how_to_sign_in() -> None:
    run = FakeRun().on("aws", "configure", "export-credentials", returns='{"Version": 1, "SessionToken": "t"}')
    with pytest.raises(CommandError, match="No usable AWS session"):
        session.aws_env(run, "nettriage")


@pytest.mark.parametrize("payload", ["null", "42", "[]", "{}"])
def test_non_object_or_empty_session_says_how_to_sign_in(payload: str) -> None:
    run = FakeRun().on("aws", "configure", "export-credentials", returns=payload)
    with pytest.raises(CommandError, match="No usable AWS session"):
        session.aws_env(run, "nettriage")


def test_account_id_is_read_from_sts() -> None:
    run = FakeRun().on("aws", "sts", "get-caller-identity", returns="123456789012\n")
    assert session.account_id(run, {}) == "123456789012"


def _signed_in() -> FakeRun:
    return (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=f"{TOOLS_CREDENTIAL_PROCESS}\n")
    )


def test_aws_ca_bundle_and_config_file_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CA_BUNDLE", "/etc/ssl/corp-ca.pem")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/custom/config")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/custom/credentials")

    env = session.aws_env(_signed_in(), "nettriage")

    assert env["AWS_CA_BUNDLE"] == "/etc/ssl/corp-ca.pem"
    assert env["AWS_CONFIG_FILE"] == "/custom/config"
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == "/custom/credentials"


@pytest.mark.parametrize(
    "var",
    [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
        "AWS_CREDENTIAL_EXPIRATION", "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_AUTHORIZATION_TOKEN",
    ],
)
def test_credential_profile_and_role_variables_never_reach_the_environment(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    monkeypatch.setenv(var, "x")
    assert var not in session.aws_env(_signed_in(), "nettriage")


def test_the_export_credentials_and_configure_calls_share_the_same_base_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_CA_BUNDLE", "/etc/ssl/corp-ca.pem")
    run = (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "configure", "get", returns=1)
        .on("aws", "configure", "set")
    )

    session.aws_env(run, "nettriage")

    [export_call] = run.called("aws", "configure", "export-credentials")
    [get_call] = run.called("aws", "configure", "get")
    set_calls = run.called("aws", "configure", "set")
    assert len(set_calls) == 2
    for call in (get_call, *set_calls):
        assert call.env == export_call.env
    assert export_call.env is not None
    assert export_call.env["AWS_CA_BUNDLE"] == "/etc/ssl/corp-ca.pem"


def test_terraform_overrides_are_stripped_but_plugin_cache_and_tf_var_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TF_LOG", "TRACE")
    monkeypatch.setenv("TF_LOG_PATH", "/tmp/tf.log")
    monkeypatch.setenv("TF_LOG_CORE", "TRACE")
    monkeypatch.setenv("TF_LOG_PROVIDER", "TRACE")
    monkeypatch.setenv("TF_CLI_ARGS", "-no-color")
    monkeypatch.setenv("TF_CLI_ARGS_apply", "-auto-approve")
    monkeypatch.setenv("TF_WORKSPACE", "prod")
    monkeypatch.setenv("TF_PLUGIN_CACHE_DIR", "/cache")
    monkeypatch.setenv("TF_VAR_GRAFANA_OTLP_AUTH", "secret")

    env = session.aws_env(_signed_in(), "nettriage")

    for dropped in ("TF_LOG", "TF_LOG_PATH", "TF_LOG_CORE", "TF_LOG_PROVIDER", "TF_CLI_ARGS", "TF_CLI_ARGS_apply", "TF_WORKSPACE"):
        assert dropped not in env
    assert env["TF_PLUGIN_CACHE_DIR"] == "/cache"
    assert env["TF_VAR_GRAFANA_OTLP_AUTH"] == "secret"
