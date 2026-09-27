import base64

import pytest

from tools.deploy import secrets
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun

PARAMETER = "/nettriage/dev/grafana-otlp-auth"


def test_token_is_read_decrypted_from_the_stage_parameter() -> None:
    run = FakeRun().on("aws", "ssm", "get-parameter", returns="dG9rZW4=\n")
    assert secrets.read_otlp_auth(run, {}, "dev") == "dG9rZW4="
    assert run.calls[0].args == [
        "aws", "ssm", "get-parameter", "--name", PARAMETER, "--with-decryption",
        "--query", "Parameter.Value", "--output", "text",
    ]


def test_missing_parameter_says_how_to_store_it() -> None:
    run = FakeRun().on("aws", "ssm", "get-parameter", returns=CommandError("ParameterNotFound"))
    with pytest.raises(CommandError, match="just store-grafana-token dev"):
        secrets.read_otlp_auth(run, {}, "dev")


def test_token_is_stored_as_a_securestring() -> None:
    run = FakeRun().on("aws", "ssm", "put-parameter")
    secrets.store_otlp_auth(run, {}, "dev", "dG9rZW4=")
    args = run.calls[0].args
    assert args[:3] == ["aws", "ssm", "put-parameter"]
    assert args[args.index("--name") + 1] == PARAMETER
    assert args[args.index("--type") + 1] == "SecureString"
    assert "--overwrite" in args


def test_empty_value_is_never_stored() -> None:
    run = FakeRun()
    with pytest.raises(CommandError, match="empty"):
        secrets.store_otlp_auth(run, {}, "dev", "   ")
    assert run.calls == []


def test_otlp_auth_value_is_base64_of_instance_and_token() -> None:
    value = secrets.otlp_auth_value(" 123456 ", " glc_abc ")
    assert base64.b64decode(value).decode() == "123456:glc_abc"


@pytest.mark.parametrize(("instance_id", "token"), [("abc", "glc_x"), ("123456", "  "), ("", "glc_x")])
def test_bad_instance_or_empty_token_is_rejected(instance_id: str, token: str) -> None:
    with pytest.raises(CommandError):
        secrets.otlp_auth_value(instance_id, token)
