import json
from pathlib import Path

import pytest

from tools.deploy import preflight
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun, ssm_names

LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
MODEL = json.dumps(
    {
        "modelDetails": {
            "modelId": "openai.gpt-oss-20b-1:0",
            "inferenceTypesSupported": ["ON_DEMAND"],
            "modelLifecycle": {"status": "ACTIVE"},
        }
    }
)


def test_tfvars_are_read(tmp_path: Path) -> None:
    path = tmp_path / "terraform.tfvars"
    path.write_text('# a comment\nlwa_layer_arn = "x"\ngrafana_otlp_endpoint    = "https://g"\n', encoding="utf-8")
    assert preflight.read_tfvars(path) == {"lwa_layer_arn": "x", "grafana_otlp_endpoint": "https://g"}


def test_a_layer_from_another_region_fails_without_calling_aws() -> None:
    run = FakeRun()
    check = preflight.layer_check(run, {}, "Lambda Web Adapter layer", LWA.replace("eu-north-1", "us-east-1"))
    assert not check.ok and "eu-north-1" in check.detail
    assert run.calls == []


def test_an_arm64_layer_in_stockholm_passes() -> None:
    run = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]})
    )
    assert preflight.layer_check(run, {}, "Lambda Web Adapter layer", LWA).ok


def test_a_missing_or_x86_only_layer_fails() -> None:
    missing = FakeRun().on("aws", "lambda", "get-layer-version-by-arn", returns=CommandError("not found"))
    x86 = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["x86_64"]})
    )
    assert not preflight.layer_check(missing, {}, "layer", LWA).ok
    assert not preflight.layer_check(x86, {}, "layer", LWA).ok


def test_the_grafana_parameter_must_exist() -> None:
    absent = FakeRun().on("aws", "ssm", "describe-parameters", returns="None\n")
    present = FakeRun().on("aws", "ssm", "describe-parameters", returns="/nettriage/dev/grafana-otlp-auth\n")
    assert not preflight.parameter_check(absent, {}, "dev").ok
    assert preflight.parameter_check(present, {}, "dev").ok


def test_a_placeholder_endpoint_fails() -> None:
    assert not preflight.endpoint_check({"grafana_otlp_endpoint": "<your Grafana Cloud OTLP endpoint>"}).ok
    assert preflight.endpoint_check({"grafana_otlp_endpoint": "https://otlp-gateway.grafana.net/otlp"}).ok


def test_a_denied_service_fails_only_its_own_check() -> None:
    run = FakeRun().on(
        "aws", "cloudfront", returns=CommandError("explicit deny in a service control policy")
    ).on("aws")
    checks = preflight.account_checks(run, {}, "123456789012")
    assert [check.name for check in checks if not check.ok] == ["CloudFront"]
    assert [check.name for check in checks] == [
        "Lambda in eu-north-1", "IAM", "CloudFront", "Budgets", "SSM in eu-north-1",
        "DynamoDB in eu-north-1", "Cognito in eu-north-1", "SQS in eu-north-1",
    ]


@pytest.mark.parametrize(
    "error",
    [
        "AccessDenied: User is not authorized",
        "explicit deny in a service control policy",
        "UnauthorizedOperation",
        "You are not authorized to perform this operation",
        "accessdenied exception",  # case-insensitive
    ],
)
def test_a_denial_error_gets_the_policy_hint(error: str) -> None:
    run = FakeRun().on("aws", "cloudfront", returns=CommandError(error)).on("aws")
    [check] = [c for c in preflight.account_checks(run, {}, "123456789012") if c.name == "CloudFront"]
    assert "AWS denied this call; the account's policies may have changed" in check.detail
    assert error in check.detail


@pytest.mark.parametrize(
    "error",
    ["Could not connect to the endpoint URL", "timed out", "Name or service not known"],
)
def test_a_non_denial_error_keeps_the_plain_message(error: str) -> None:
    run = FakeRun().on("aws", "cloudfront", returns=CommandError(error)).on("aws")
    [check] = [c for c in preflight.account_checks(run, {}, "123456789012") if c.name == "CloudFront"]
    assert "AWS denied this call" not in check.detail
    assert check.detail == error


def test_report_prints_each_check_and_fails_if_any_fails(capsys: pytest.CaptureFixture[str]) -> None:
    ok = preflight.report([preflight.Check("A", True), preflight.Check("B", False, "why")])
    out = capsys.readouterr().out
    assert not ok
    assert "PASS  A" in out
    assert "FAIL  B  why" in out


def test_a_layer_named_like_the_region_in_another_region_fails() -> None:
    run = FakeRun()
    check = preflight.layer_check(run, {}, "layer", "arn:aws:lambda:us-east-1:753240598075:layer:eu-north-1:5")
    assert not check.ok and "eu-north-1" in check.detail
    assert run.calls == []


def test_tfvars_values_with_inline_comments_are_read(tmp_path: Path) -> None:
    path = tmp_path / "terraform.tfvars"
    path.write_text('lwa_layer_arn = "x"  # pinned\ngrafana_otlp_endpoint = "https://g" // note\n', encoding="utf-8")
    assert preflight.read_tfvars(path) == {"lwa_layer_arn": "x", "grafana_otlp_endpoint": "https://g"}


def test_stage_checks_names_and_order() -> None:
    run = (
        FakeRun()
        .on("aws", "lambda", "get-layer-version-by-arn", returns='{"CompatibleArchitectures": ["arm64"]}')
        .on("aws", "bedrock", "get-foundation-model", returns=MODEL)
        .on("aws", "ssm", "describe-parameters", returns=ssm_names())
        .on("aws")
    )
    tfvars = {
        "lwa_layer_arn": "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30",
        "otel_collector_layer_arn": "arn:aws:lambda:eu-north-1:753240598075:layer:OtelLayerArm64:1",
        "grafana_otlp_endpoint": "https://otlp-gateway.grafana.net/otlp",
        "bedrock_region": "eu-north-1",
        "bedrock_model_id": "openai.gpt-oss-20b-1:0",
    }
    checks = preflight.stage_checks(run, {}, "dev", "123456789012", tfvars)
    assert [check.name for check in checks] == [
        "Terraform state bucket", "Grafana token in SSM", "Database connection in SSM",
        "Grafana OTLP endpoint in terraform.tfvars",
        "Lambda Web Adapter layer", "OpenTelemetry collector layer",
        "Triage model on Bedrock",
    ]
    assert all(check.ok for check in checks)


def test_a_layer_without_listed_architectures_passes() -> None:
    run = FakeRun().on(
        "aws", "lambda", "get-layer-version-by-arn", returns="{}"
    )
    assert preflight.layer_check(run, {}, "layer", LWA).ok


def test_a_missing_database_connection_says_how_to_store_it() -> None:
    run = FakeRun().on(
        "aws", "ssm", "describe-parameters", returns=ssm_names(missing=["/nettriage/dev/db/owner-url"])
    )
    run.on("aws", "lambda", returns='{"CompatibleArchitectures": ["arm64"]}').on("aws")

    checks = {check.name: check for check in preflight.stage_checks(run, {}, "dev", "123456789012", {})}

    assert not checks["Database connection in SSM"].ok
    assert checks["Database connection in SSM"].detail == "missing; run: just store-database-url dev"


def test_the_triage_model_runs_on_demand_in_its_region() -> None:
    run = FakeRun().on("aws", "bedrock", "get-foundation-model", returns=MODEL)

    check = preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0")

    assert check.ok
    [call] = run.called("aws", "bedrock", "get-foundation-model")
    assert call.args[call.args.index("--model-identifier") + 1] == "openai.gpt-oss-20b-1:0"
    assert call.args[call.args.index("--region") + 1] == "eu-north-1"


def test_an_answer_that_isnt_json_fails() -> None:
    run = FakeRun().on("aws", "bedrock", "get-foundation-model", returns="")

    assert not preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0").ok


@pytest.mark.parametrize(
    "details",
    [
        {"inferenceTypesSupported": ["PROVISIONED"], "modelLifecycle": {"status": "ACTIVE"}},
        {"inferenceTypesSupported": ["INFERENCE_PROFILE"], "modelLifecycle": {"status": "ACTIVE"}},
        {"inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "RETIRED"}},
    ],
)
def test_a_model_that_isnt_on_demand_and_active_fails(details: dict[str, object]) -> None:
    run = FakeRun().on(
        "aws", "bedrock", "get-foundation-model", returns=json.dumps({"modelDetails": details})
    )

    assert not preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0").ok


def test_a_legacy_model_still_passes_with_a_warning() -> None:
    """Bedrock keeps a legacy model invokable for at least six months: an unrelated deploy must
    not wait for the switch to another model."""
    details = {"inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "LEGACY"}}
    run = FakeRun().on(
        "aws", "bedrock", "get-foundation-model", returns=json.dumps({"modelDetails": details})
    )

    check = preflight.model_check(run, {}, "eu-north-1", "openai.gpt-oss-20b-1:0")

    assert check.ok
    assert "LEGACY" in check.detail
    assert "plan a switch" in check.detail


def test_a_model_bedrock_doesnt_offer_there_fails_with_a_hint() -> None:
    run = FakeRun().on(
        "aws", "bedrock", "get-foundation-model", returns=CommandError("ResourceNotFoundException")
    )

    check = preflight.model_check(run, {}, "eu-north-1", "mistral.ministral-3-8b-instruct")

    assert not check.ok
    assert "terraform.tfvars" in check.detail


@pytest.mark.parametrize(("region", "model"), [("eu-west-1", "x"), ("eu-north-1", "")])
def test_a_region_the_account_cant_call_or_no_model_fails_without_calling_aws(
    region: str, model: str
) -> None:
    run = FakeRun()

    assert not preflight.model_check(run, {}, region, model).ok
    assert run.called("aws") == []
