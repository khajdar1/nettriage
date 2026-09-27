import json
from pathlib import Path

import pytest

from tools.deploy import preflight
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun

LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"


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
    ]


def test_report_prints_each_check_and_fails_if_any_fails(capsys: pytest.CaptureFixture[str]) -> None:
    ok = preflight.report([preflight.Check("A", True), preflight.Check("B", False, "why")])
    out = capsys.readouterr().out
    assert not ok
    assert "PASS  A" in out
    assert "FAIL  B  why" in out
