import json
from pathlib import Path

from tools.deploy import terraform
from tools.tests.deploy_fakes import FakeRun

PLAN = {
    "resource_changes": [
        {
            "address": "module.app.aws_lambda_function.api",
            "change": {"actions": ["update"], "after": {"env": {"GRAFANA_OTLP_AUTH": "s3cr3t"}}},
        },
        {"address": "module.edge.aws_s3_bucket.web", "change": {"actions": ["no-op"], "after": {}}},
        {"address": "data.aws_caller_identity.current", "change": {"actions": ["read"], "after": {}}},
        {
            "address": "aws_lambda_permission.cloudfront_invoke",
            "change": {"actions": ["delete", "create"], "after": {}},
        },
    ]
}


def test_backend_is_the_stockholm_state_bucket_with_native_locking() -> None:
    assert terraform.backend_args("nettriage-tfstate-123456789012", "envs/dev/terraform.tfstate") == [
        "-backend-config=bucket=nettriage-tfstate-123456789012",
        "-backend-config=key=envs/dev/terraform.tfstate",
        "-backend-config=region=eu-north-1",
        "-backend-config=use_lockfile=true",
    ]


def test_planned_changes_list_actions_and_addresses_only() -> None:
    lines = terraform.planned_changes(json.dumps(PLAN))
    assert lines == [
        "update module.app.aws_lambda_function.api",
        "delete/create aws_lambda_permission.cloudfront_invoke",
    ]
    assert "s3cr3t" not in "\n".join(lines)


def test_apply_is_interactive_so_the_owner_confirms(tmp_path: Path) -> None:
    run = FakeRun().on("terraform", "apply")
    terraform.apply(run, {}, tmp_path)
    assert run.calls[0].interactive is True
    assert "-auto-approve" not in run.calls[0].args


def test_outputs_are_flattened_to_strings(tmp_path: Path) -> None:
    run = FakeRun().on("terraform", "output", returns=json.dumps({"web_bucket": {"value": "b"}}))
    assert terraform.outputs(run, {}, tmp_path) == {"web_bucket": "b"}
