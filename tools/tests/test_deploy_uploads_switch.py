"""Pausing and resuming uploads from the owner's machine (spec §9.7, runbook Part C)."""

import pytest

from tools.deploy import __main__ as cli
from tools.tests.deploy_fakes import signed_in

NAME = "/nettriage/dev/kill/uploads-enabled"


@pytest.mark.parametrize(
    ("state", "value", "shown"), [("off", "false", "paused"), ("on", "true", "on")]
)
def test_the_uploads_switch_is_set_then_read_back(
    state: str, value: str, shown: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = (
        signed_in()
        .on("aws", "ssm", "put-parameter")
        .on("aws", "ssm", "get-parameter", returns=f"{value}\n")
    )

    code = cli.main(["uploads", state], run=run)

    [put] = run.called("aws", "ssm", "put-parameter")
    assert code == 0
    # Passed as an argument list, so no shell (Git Bash included) can rewrite the name.
    assert put.args[put.args.index("--name") + 1] == NAME
    assert put.args[put.args.index("--value") + 1] == value
    assert run.first("aws", "ssm", "get-parameter") > run.first("aws", "ssm", "put-parameter")
    assert f"Uploads in dev are {shown}" in capsys.readouterr().out


def test_a_switch_that_reads_back_wrong_is_a_stop(capsys: pytest.CaptureFixture[str]) -> None:
    run = (
        signed_in()
        .on("aws", "ssm", "put-parameter")
        .on("aws", "ssm", "get-parameter", returns="true\n")
    )

    code = cli.main(["uploads", "off"], run=run)

    assert code == 1
    assert "STOP: " in capsys.readouterr().err
