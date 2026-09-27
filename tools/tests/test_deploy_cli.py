import base64
import json
from pathlib import Path

import pytest

from tools.deploy import __main__ as cli
from tools.deploy import config
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import Call, FakeRun, runs, signed_in

SHA = "d" * 40
LWA = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
OTEL = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
OUTPUTS = json.dumps(
    {
        "cloudfront_domain": {"value": "d111.cloudfront.net"},
        "distribution_id": {"value": "E123"},
        "web_bucket": {"value": "nettriage-dev-web-1"},
        "function_url": {"value": "https://fn.lambda-url.eu-north-1.on.aws/"},
    }
)


@pytest.fixture
def stage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "terraform.tfvars").write_text(
        f'lwa_layer_arn = "{LWA}"\notel_collector_layer_arn = "{OTEL}"\n'
        'grafana_otlp_endpoint = "https://otlp.example/otlp"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "stage_dir", lambda stage: tmp_path)
    return tmp_path


def healthy_account(run: FakeRun) -> FakeRun:
    """Every read-only AWS check passes and the token is stored."""
    return (
        run.on("aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]}))
        .on("aws", "ssm", "describe-parameters", returns="/nettriage/dev/grafana-otlp-auth\n")
        .on("aws", "ssm", "get-parameter", returns="dG9rZW4=\n")
        .on("aws")
    )


def main_checkout(run: FakeRun) -> FakeRun:
    return (
        run.on("git", "rev-parse", "--abbrev-ref", returns="main\n")
        .on("git", "status", returns="")
        .on("git", "fetch")
        .on("git", "rev-parse", returns=f"{SHA}\n")
    )


def deployable() -> FakeRun:
    run = main_checkout(signed_in())
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    run.on("gh", "run", "download").on("terraform", "output", returns=OUTPUTS).on("terraform")
    return healthy_account(run)


def test_deploy_ships_ci_artifacts_of_a_green_main_commit_then_smoke_tests(stage_dir: Path) -> None:
    run = deployable()
    smoke_argv: list[list[str]] = []

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: smoke_argv.append(argv) or 0)

    workflows = [call.args[call.args.index("--workflow") + 1] for call in run.called("gh", "run", "list")]
    assert workflows == ["ci.yml", "codeql.yml"]
    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "web-dist",
    ]
    assert run.first("gh", "run", "download") < run.first("terraform", "apply") < run.first("aws", "s3", "sync")
    [init] = run.called("terraform", "init")
    assert init.cwd == stage_dir
    assert "-backend-config=bucket=nettriage-tfstate-123456789012" in init.args
    assert "-backend-config=key=envs/dev/terraform.tfstate" in init.args
    assert "-backend-config=region=eu-north-1" in init.args
    [apply] = run.called("terraform", "apply")
    assert apply.interactive
    assert apply.env is not None
    assert apply.env["TF_VAR_app_version"] == SHA
    assert apply.env["TF_VAR_grafana_otlp_auth"] == "dG9rZW4="
    assert apply.env["TF_VAR_lambda_zip_path"].endswith("backend.zip")
    assert smoke_argv == [[
        "--base-url", "https://d111.cloudfront.net",
        "--function-url", "https://fn.lambda-url.eu-north-1.on.aws/",
        "--version", SHA,
    ]]


@pytest.mark.parametrize(
    "listing",
    [runs(), runs((9, "completed", "failure", "push")), runs((9, "in_progress", "", "push"))],
)
def test_nothing_changes_in_aws_unless_ci_is_green(stage_dir: Path, listing: str) -> None:
    run = healthy_account(main_checkout(signed_in()).on("gh", "run", "list", returns=listing))
    with pytest.raises(CommandError):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws", "s3", "sync") == []
    assert run.called("aws", "cloudfront", "create-invalidation") == []


def test_deploy_stops_before_git_and_github_when_preflight_fails(stage_dir: Path) -> None:
    run = healthy_account(signed_in().on("aws", "cloudfront", returns=CommandError("explicit deny")))
    with pytest.raises(CommandError, match="Preflight failed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("git") == [] and run.called("gh") == [] and run.called("terraform") == []


def test_failed_smoke_tests_fail_the_deploy(stage_dir: Path) -> None:
    with pytest.raises(CommandError, match="Smoke tests failed"):
        cli.deploy(deployable(), {}, "dev", smoke_main=lambda argv: 1)


def planning(listing: str, plan_json: str = '{"resource_changes": []}') -> FakeRun:
    run = signed_in().on("git", "rev-parse", "HEAD", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=listing).on("gh", "run", "download").on("gh", "pr", "comment")
    run.on("terraform", "show", returns=plan_json).on("terraform")
    return healthy_account(run)


def test_plan_posts_addresses_only_to_the_pr(stage_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_json = json.dumps(
        {"resource_changes": [{"address": "module.app.aws_lambda_function.api",
                               "change": {"actions": ["create"], "after": {"secret": "dG9rZW4="}}}]}
    )
    run = planning(runs((5, "completed", "success", "pull_request")), plan_json)

    changes = cli.plan(run, {}, "dev", post_comment=True)

    assert changes == ["create module.app.aws_lambda_function.api"]
    assert len(run.called("gh", "pr", "comment")) == 1
    out = capsys.readouterr().out
    assert "### Terraform plan: dev (ddddddd)" in out
    assert "dG9rZW4=" not in out


def test_plan_with_no_comment_leaves_the_pr_alone(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "success", "pull_request")))
    cli.plan(run, {}, "dev", post_comment=False)
    assert run.called("gh", "pr", "comment") == []


def test_plan_without_a_finished_ci_run_stops(stage_dir: Path) -> None:
    run = planning(runs())
    with pytest.raises(CommandError, match="CI hasn't finished"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("terraform") == []


@pytest.fixture
def bootstrap_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    boot = tmp_path / "bootstrap"
    boot.mkdir()
    (boot / "main.tf").write_text("# the stack\n", encoding="utf-8")
    (boot / "backend.tf").write_text('terraform {\n  backend "s3" {}\n}\n', encoding="utf-8")
    monkeypatch.setattr(config, "BOOTSTRAP_DIR", boot)
    return boot


def test_first_bootstrap_applies_locally_then_pushes_state_into_the_new_bucket(bootstrap_dir: Path) -> None:
    seen: dict[str, bool] = {}

    def apply(call: Call) -> str:
        assert call.cwd is not None
        seen["backend_in_scratch"] = (call.cwd / "backend.tf").exists()
        (call.cwd / "terraform.tfstate").write_text("{}", encoding="utf-8")
        return ""

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    cli.bootstrap(run, {}, "owner@example.com", "")

    assert seen["backend_in_scratch"] is False
    [apply_call] = run.called("terraform", "apply")
    assert "-var=budget_email=owner@example.com" in apply_call.args
    [push] = run.called("terraform", "state", "push")
    assert push.cwd == bootstrap_dir
    repo_init = [call for call in run.called("terraform", "init") if call.cwd == bootstrap_dir]
    assert "-backend-config=key=bootstrap/terraform.tfstate" in repo_init[0].args


def test_a_failed_first_apply_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        raise CommandError("`terraform apply` failed with exit code 1.")

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    with pytest.raises(CommandError, match="partial state is saved"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'
    assert run.called("terraform", "state", "push") == []


def test_later_bootstraps_apply_against_the_bucket(bootstrap_dir: Path) -> None:
    run = signed_in().on("aws", "s3api", "head-bucket", returns=0).on("terraform").on("aws")
    cli.bootstrap(run, {}, "owner@example.com", "arn:aws:ce::123456789012:anomalymonitor/x")
    [apply] = run.called("terraform", "apply")
    assert apply.cwd == bootstrap_dir
    assert "-var=anomaly_monitor_arn=arn:aws:ce::123456789012:anomalymonitor/x" in apply.args
    assert run.called("terraform", "state", "push") == []


def test_store_grafana_token_prompts_and_never_prints_the_token(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun().on("aws", "ssm", "put-parameter")
    cli.store_grafana_token(run, {}, "dev", ask_id=lambda prompt: "123456", ask_secret=lambda prompt: "glc_secret")
    args = run.calls[0].args
    assert base64.b64decode(args[args.index("--value") + 1]).decode() == "123456:glc_secret"
    captured = capsys.readouterr()
    assert "glc_secret" not in captured.out + captured.err


def test_main_stops_with_a_sign_in_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "PLUGIN_CACHE", tmp_path / "cache")
    run = FakeRun().on("aws", "configure", "export-credentials", returns=CommandError("expired"))
    assert cli.main(["preflight"], run=run) == 1
    assert "STOP: No usable AWS session" in capsys.readouterr().err


def test_main_preflight_passes_on_a_healthy_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage_dir: Path
) -> None:
    monkeypatch.setattr(config, "PLUGIN_CACHE", tmp_path / "cache")
    assert cli.main(["preflight", "--stage", "dev"], run=healthy_account(signed_in())) == 0
