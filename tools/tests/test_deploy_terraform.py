import json
from pathlib import Path

import pytest

from tools.deploy import terraform
from tools.deploy.runner import CommandError, Result
from tools.tests.deploy_fakes import FakeRun

# Real terraform plan -json message shapes (trimmed to the fields we read).
PLAN_LINES = "\n".join(
    json.dumps(message)
    for message in [
        {
            "@level": "info",
            "@message": "module.app.aws_lambda_function.api: Plan to update in-place",
            "type": "planned_change",
            "change": {
                "action": "update",
                "resource": {"addr": "module.app.aws_lambda_function.api"},
                "after_unknown": {"environment": {"GRAFANA_OTLP_AUTH": "s3cr3t"}},
            },
        },
        {
            "@level": "info",
            "@message": "module.edge.aws_s3_bucket.web: no changes",
            "type": "planned_change",
            "change": {"action": "noop", "resource": {"addr": "module.edge.aws_s3_bucket.web"}},
        },
        {
            "@level": "info",
            "@message": "data.aws_caller_identity.current: Reading...",
            "type": "planned_change",
            "change": {"action": "read", "resource": {"addr": "data.aws_caller_identity.current"}},
        },
        {
            "@level": "info",
            "@message": "aws_lambda_permission.cloudfront_invoke: Plan to replace",
            "type": "planned_change",
            "change": {"action": "replace", "resource": {"addr": "aws_lambda_permission.cloudfront_invoke"}},
        },
        {
            "@level": "info",
            "@message": "Terraform will perform the following actions:",
            "type": "change_summary",
            "changes": {"add": 1, "change": 1, "remove": 0},
        },
    ]
)


def test_backend_is_the_stockholm_state_bucket_with_native_locking() -> None:
    assert terraform.backend_args("nettriage-tfstate-123456789012", "envs/dev/terraform.tfstate") == [
        "-backend-config=bucket=nettriage-tfstate-123456789012",
        "-backend-config=key=envs/dev/terraform.tfstate",
        "-backend-config=region=eu-north-1",
        "-backend-config=use_lockfile=true",
    ]


def test_planned_changes_list_actions_and_addresses_only() -> None:
    lines = terraform.planned_changes(PLAN_LINES)
    assert lines == [
        "update module.app.aws_lambda_function.api",
        "replace aws_lambda_permission.cloudfront_invoke",
    ]
    assert "s3cr3t" not in "\n".join(lines)


def test_planned_changes_ignores_blank_lines() -> None:
    assert terraform.planned_changes("\n\n") == []


def test_apply_is_interactive_so_the_owner_confirms(tmp_path: Path) -> None:
    run = FakeRun().on("terraform", "apply")
    terraform.apply(run, {}, tmp_path)
    assert run.calls[0].interactive is True
    assert "-auto-approve" not in run.calls[0].args


def test_outputs_are_flattened_to_strings(tmp_path: Path) -> None:
    run = FakeRun().on("terraform", "output", returns=json.dumps({"web_bucket": {"value": "b"}}))
    assert terraform.outputs(run, {}, tmp_path) == {"web_bucket": "b"}


def test_plan_writes_no_plan_file_and_reads_json_lines(tmp_path: Path) -> None:
    run = FakeRun().on("terraform", "plan", returns=PLAN_LINES)

    changes = terraform.plan(run, {}, tmp_path)

    assert changes == [
        "update module.app.aws_lambda_function.api",
        "replace aws_lambda_permission.cloudfront_invoke",
    ]
    [call] = run.called("terraform", "plan")
    assert call.cwd == tmp_path
    assert "-json" in call.args
    assert not any(arg.startswith("-out") for arg in call.args)


def test_plan_raises_with_the_error_diagnostics_but_not_the_raw_json(tmp_path: Path) -> None:
    diagnostics = "\n".join(
        json.dumps(message)
        for message in [
            {"@level": "error", "@message": "Invalid backend configuration", "type": "diagnostic"},
            {"@level": "error", "@message": "region is required", "type": "diagnostic"},
        ]
    )
    run = FakeRun().on("terraform", "plan", returns=lambda call: Result(1, diagnostics))

    with pytest.raises(CommandError, match=r"terraform plan.*Invalid backend configuration; region is required"):
        terraform.plan(run, {}, tmp_path)
