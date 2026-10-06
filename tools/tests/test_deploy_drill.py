"""The restore drill on demand (Plan 7a §2.5): `just restore-drill-<stage>` invokes the ops
function once, with the owner's session, and says what it restored."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.deploy import __main__ as cli
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import Call, signed_in

RESTORED = {"job": "restore_drill", "day": "2026-10-07", "tables": 15, "rows": 1234}


def invoked(answer: dict[str, object], function_error: str = "") -> Callable[[Call], str]:
    """`aws lambda invoke`: the function's answer goes to the file named last, and the call's
    metadata to stdout, with FunctionError when the function raised."""

    def respond(call: Call) -> str:
        Path(call.args[-1]).write_text(json.dumps(answer), encoding="utf-8")
        metadata: dict[str, object] = {"StatusCode": 200, "ExecutedVersion": "$LATEST"}
        if function_error:
            metadata["FunctionError"] = function_error
        return json.dumps(metadata)

    return respond


def test_the_drill_invokes_the_ops_function_once_and_says_what_it_restored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(RESTORED))

    code = cli.main(["restore-drill", "--stage", "dev"], run=run)

    assert code == 0
    [invoke] = run.called("aws", "lambda", "invoke")
    assert invoke.args[invoke.args.index("--function-name") + 1] == "nettriage-dev-ops"
    assert json.loads(invoke.args[invoke.args.index("--payload") + 1]) == {"job": "restore_drill"}
    assert invoke.args[invoke.args.index("--cli-binary-format") + 1] == "raw-in-base64-out"
    # The drill may use the function's whole 600 s, and a retry would start a second drill.
    assert int(invoke.args[invoke.args.index("--cli-read-timeout") + 1]) > 600
    assert invoke.env is not None
    assert invoke.env["AWS_MAX_ATTEMPTS"] == "1"
    assert (
        "The restore drill restored the backup of 2026-10-07: 15 tables and 1234 rows, "
        "every table's count matching its manifest."
    ) in capsys.readouterr().out


@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        ("DrillFailed", "The restored backup of 2026-10-07 differs from its manifest in findings, uploads."),
        ("NoBackup", "There's no backup to restore yet."),
    ],
)
def test_the_drills_own_failures_are_told_as_they_are(
    error_type: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = {"errorType": error_type, "errorMessage": message, "stackTrace": ["  File ..."]}
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(failure, "Unhandled"))

    code = cli.main(["restore-drill"], run=run)

    assert code == 1
    assert f"STOP: {message}" in capsys.readouterr().err


def test_any_other_failure_is_named_by_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    failure = {
        "errorType": "OperationalError",
        "errorMessage": 'connection to server at "ep-quiet-sun-123456.eu-central-1.aws.neon.tech" '
        'failed: password authentication failed for user "app_backup"',
    }
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(failure, "Unhandled"))

    code = cli.main(["restore-drill"], run=run)

    err = capsys.readouterr().err
    assert code == 1
    assert "STOP: The restore drill failed with OperationalError." in err
    assert "/aws/lambda/nettriage-dev-ops" in err
    assert "ep-quiet-sun" not in err
    assert "app_backup" not in err


def test_a_failure_the_function_names_by_its_type_is_told_by_that_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = {"errorType": "JobFailed", "errorMessage": "RestoreFailed"}
    run = signed_in().on("aws", "lambda", "invoke", returns=invoked(failure, "Unhandled"))

    code = cli.main(["restore-drill"], run=run)

    assert code == 1
    assert "STOP: The restore drill failed with RestoreFailed." in capsys.readouterr().err


def test_a_stage_without_the_ops_function_says_to_deploy_it_first(
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = CommandError("`aws lambda` failed with exit code 254.")
    run = signed_in().on("aws", "lambda", "invoke", returns=missing)

    code = cli.main(["restore-drill"], run=run)

    err = capsys.readouterr().err
    assert code == 1
    assert "Couldn't invoke nettriage-dev-ops" in err
    assert "just deploy-dev" in err
