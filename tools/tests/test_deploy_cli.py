import base64
import json
import sys
from pathlib import Path

import pytest

from tools.deploy import __main__ as cli
from tools.deploy import config
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import (
    OWNER_URL,
    Call,
    FakeRun,
    runs,
    signed_in,
    ssm_names,
    ssm_values,
)

SHA = "d" * 40
SYNCED = "Reference data synced: 3 detectors, 12 ATT&CK techniques.\n"
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
        'grafana_otlp_endpoint = "https://otlp.example/otlp"\n'
        'bedrock_region = "eu-north-1"\nbedrock_model_id = "openai.gpt-oss-20b-1:0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "stage_dir", lambda stage: tmp_path)
    return tmp_path


STORED = {"/nettriage/dev/grafana-otlp-auth": "dG9rZW4=", "/nettriage/dev/db/owner-url": OWNER_URL}


def healthy_account(run: FakeRun) -> FakeRun:
    """Every read-only AWS check passes, and the Grafana token, the database owner's URL and
    the app roles' logins are all stored."""
    return (
        run.on("aws", "lambda", "get-layer-version-by-arn", returns=json.dumps({"CompatibleArchitectures": ["arm64"]}))
        .on("aws", "bedrock", "get-foundation-model", returns=json.dumps(
            {"modelDetails": {"inferenceTypesSupported": ["ON_DEMAND"], "modelLifecycle": {"status": "ACTIVE"}}}
        ))
        .on("aws", "ssm", "describe-parameters", returns=ssm_names())
        .on("aws", "ssm", "get-parameter", returns=ssm_values(STORED))
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
    run.on(sys.executable, "-m", "alembic")
    run.on(sys.executable, "-m", "nettriage.adapters.reference_data", returns=SYNCED)
    return healthy_account(run)


def test_deploy_ships_ci_artifacts_of_a_green_main_commit_then_smoke_tests(stage_dir: Path) -> None:
    run = deployable()
    smoke_argv: list[list[str]] = []

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: smoke_argv.append(argv) or 0)

    workflows = [call.args[call.args.index("--workflow") + 1] for call in run.called("gh", "run", "list")]
    assert workflows == ["ci.yml", "codeql.yml"]
    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "pg-client-zip", "web-dist",
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
    assert apply.env["TF_VAR_pg_client_zip_path"].endswith("pg-client.zip")
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
    run = main_checkout(signed_in()).on("gh", "run", "list", returns=listing)
    with pytest.raises(CommandError, match="ci.yml"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws") == []


def test_codeql_red_blocks_the_deploy(stage_dir: Path) -> None:
    run = main_checkout(signed_in())
    # The specific codeql.yml rule must come before the generic "gh run list" success rule.
    run.on("gh", "run", "list", "--workflow", "codeql.yml", returns=runs((11, "completed", "failure", "push")))
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    with pytest.raises(CommandError, match="codeql.yml"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws") == []


def _run_on_another_branch() -> FakeRun:
    return FakeRun().on("git", "rev-parse", "--abbrev-ref", returns="feature\n")


def _run_with_a_dirty_tree() -> FakeRun:
    return FakeRun().on("git", "rev-parse", "--abbrev-ref", returns="main\n").on(
        "git", "status", returns=" M file.py\n"
    )


@pytest.mark.parametrize(
    "build, match",
    [(_run_on_another_branch, "Deploys run from main"), (_run_with_a_dirty_tree, "uncommitted changes")],
    ids=["other-branch", "dirty-tree"],
)
def test_a_dirty_or_other_branch_checkout_never_reaches_terraform(
    stage_dir: Path, build, match: str
) -> None:
    run = build()
    with pytest.raises(CommandError, match=match):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []
    assert run.called("aws") == []
    assert run.called("gh") == []


def test_a_checkout_that_turns_dirty_during_the_deploy_makes_no_terraform_call(stage_dir: Path) -> None:
    """require_clean_main's own check passes; the tree turns dirty only afterwards (while CI and
    the artifact downloads are checked), so the pre-apply recheck must catch it."""
    porcelain_calls = {"n": 0}

    def porcelain_status(call: Call) -> str:
        porcelain_calls["n"] += 1
        return "" if porcelain_calls["n"] == 1 else " M file.py\n"

    run = healthy_account(signed_in())
    run.on("git", "rev-parse", "--abbrev-ref", returns="main\n")
    run.on("git", "status", "--porcelain", returns=porcelain_status)
    run.on("git", "fetch")
    run.on("git", "rev-parse", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    run.on("gh", "run", "download")

    with pytest.raises(CommandError, match="checkout changed during the deploy"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called("terraform") == []
    assert run.called("aws", "s3", "sync") == []
    assert run.called("aws", "cloudfront", "create-invalidation") == []


def test_deploy_stops_before_terraform_when_preflight_fails(stage_dir: Path) -> None:
    """R16: preflight (the only guard that makes AWS calls) runs last, after every local and
    GitHub guard, but it must still stop the deploy before Terraform touches anything."""
    run = main_checkout(signed_in().on("aws", "cloudfront", returns=CommandError("explicit deny")))
    run.on("gh", "run", "list", returns=runs((9, "completed", "success", "push")))
    run = healthy_account(run)
    with pytest.raises(CommandError, match="Preflight failed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)
    assert run.called("terraform") == []


def test_failed_smoke_tests_fail_the_deploy(stage_dir: Path) -> None:
    with pytest.raises(CommandError, match="Smoke tests failed"):
        cli.deploy(deployable(), {}, "dev", smoke_main=lambda argv: 1)


def planning(listing: str, plan_output: str = "") -> FakeRun:
    run = signed_in().on("git", "status", "--porcelain", returns="").on("git", "rev-parse", "HEAD", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=listing).on("gh", "run", "download").on("gh", "pr", "comment")
    run.on("terraform", "plan", returns=plan_output).on("terraform")
    return healthy_account(run)


def test_plan_posts_addresses_only_to_the_pr(stage_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_output = json.dumps(
        {
            "type": "planned_change",
            "change": {
                "action": "create",
                "resource": {"addr": "module.app.aws_lambda_function.api"},
                "after_unknown": {"secret": "dG9rZW4="},
            },
        }
    )
    run = planning(runs((5, "completed", "success", "pull_request")), plan_output)

    changes = cli.plan(run, {}, "dev", post_comment=True)

    assert changes == ["create module.app.aws_lambda_function.api"]
    assert len(run.called("gh", "pr", "comment")) == 1
    [plan_call] = run.called("terraform", "plan")
    assert "-json" in plan_call.args
    assert not any(arg.startswith("-out") for arg in plan_call.args)
    out = capsys.readouterr().out
    assert "### Terraform plan: dev (ddddddd)" in out
    assert "dG9rZW4=" not in out


def test_plan_gives_terraform_the_ci_built_postgres_layer(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "success", "pull_request")))

    cli.plan(run, {}, "dev", post_comment=False)

    assert [call.args[call.args.index("--name") + 1] for call in run.called("gh", "run", "download")] == [
        "backend-zip", "pg-client-zip",
    ]
    [plan_call] = run.called("terraform", "plan")
    assert plan_call.env is not None
    assert plan_call.env["TF_VAR_pg_client_zip_path"].endswith("pg-client.zip")


def test_plan_with_no_comment_leaves_the_pr_alone(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "success", "pull_request")))
    cli.plan(run, {}, "dev", post_comment=False)
    assert run.called("gh", "pr", "comment") == []


def test_plan_refuses_a_missing_ci_run(stage_dir: Path) -> None:
    run = planning(runs())
    with pytest.raises(CommandError, match="No ci.yml run found"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("terraform") == []


def test_plan_refuses_a_completed_but_failed_ci_run(stage_dir: Path) -> None:
    run = planning(runs((5, "completed", "failure", "pull_request")))
    with pytest.raises(CommandError, match="only green commits are planned or deployed"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("terraform") == []


def test_plan_refuses_a_dirty_tree_before_any_gh_or_aws_call(stage_dir: Path) -> None:
    run = FakeRun().on("git", "status", "--porcelain", returns=" M file.py\n")
    with pytest.raises(CommandError, match="uncommitted changes"):
        cli.plan(run, {}, "dev", post_comment=True)
    assert run.called("gh") == []
    assert run.called("aws") == []
    assert run.called("terraform") == []


def test_plan_refuses_a_tree_that_turns_dirty_after_the_ci_check(stage_dir: Path) -> None:
    """require_clean_tree's own check passes; the tree turns dirty only afterwards (while the CI
    check and artifact download run), so the pre-terraform recheck must catch it."""
    porcelain_calls = {"n": 0}

    def porcelain_status(call: Call) -> str:
        porcelain_calls["n"] += 1
        return "" if porcelain_calls["n"] == 1 else " M file.py\n"

    run = healthy_account(signed_in()).on("git", "status", "--porcelain", returns=porcelain_status)
    run.on("git", "rev-parse", "HEAD", returns=f"{SHA}\n")
    run.on("gh", "run", "list", returns=runs((5, "completed", "success", "pull_request")))
    run.on("gh", "run", "download")

    with pytest.raises(CommandError, match="checkout changed during the plan"):
        cli.plan(run, {}, "dev", post_comment=True)

    assert run.called("terraform") == []
    assert run.called("gh", "pr", "comment") == []


def test_plan_refuses_a_head_that_moves_after_the_ci_check(stage_dir: Path) -> None:
    head_calls = {"n": 0}

    def head_sha_answer(call: Call) -> str:
        head_calls["n"] += 1
        return f"{SHA}\n" if head_calls["n"] == 1 else f"{'c' * 40}\n"

    run = healthy_account(signed_in()).on("git", "status", "--porcelain", returns="")
    run.on("git", "rev-parse", "HEAD", returns=head_sha_answer)
    run.on("gh", "run", "list", returns=runs((5, "completed", "success", "pull_request")))
    run.on("gh", "run", "download")

    with pytest.raises(CommandError, match="checkout changed during the plan"):
        cli.plan(run, {}, "dev", post_comment=True)

    assert run.called("terraform") == []
    assert run.called("gh", "pr", "comment") == []


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
    assert "-no-color" in push.args
    repo_init = [call for call in run.called("terraform", "init") if call.cwd == bootstrap_dir]
    assert "-backend-config=key=bootstrap/terraform.tfstate" in repo_init[0].args
    assert all("-no-color" in call.args for call in run.called("terraform", "init"))
    assert all("-lockfile=readonly" in call.args for call in run.called("terraform", "init"))


def test_a_failed_first_apply_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        raise CommandError("`terraform apply` failed with exit code 1.")

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    with pytest.raises(CommandError, match="state is saved in"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'
    assert run.called("terraform", "state", "push") == []


def test_a_failed_first_state_push_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        return ""

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply)
    run.on("terraform", "state", "push", returns=CommandError("`terraform state push` failed with exit code 1."))
    run.on("terraform").on("aws")

    with pytest.raises(CommandError, match="state is saved in"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'


def test_a_ctrl_c_during_first_bootstrap_keeps_its_partial_state(bootstrap_dir: Path) -> None:
    def apply(call: Call) -> str:
        assert call.cwd is not None
        (call.cwd / "terraform.tfstate").write_text('{"partial": true}', encoding="utf-8")
        raise KeyboardInterrupt

    run = signed_in().on("aws", "s3api", "head-bucket", returns=254)
    run.on("terraform", "apply", returns=apply).on("terraform").on("aws")

    with pytest.raises(CommandError, match="state is saved in"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert (bootstrap_dir / "terraform.tfstate.recovered").read_text(encoding="utf-8") == '{"partial": true}'
    assert run.called("terraform", "state", "push") == []


def test_bootstrap_refuses_to_run_again_over_a_saved_state(bootstrap_dir: Path) -> None:
    (bootstrap_dir / "terraform.tfstate.recovered").write_text('{"partial": true}', encoding="utf-8")
    run = FakeRun()

    with pytest.raises(CommandError, match="A saved bootstrap state exists"):
        cli.bootstrap(run, {}, "owner@example.com", "")

    assert run.calls == []


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


def test_store_grafana_token_never_leaks_the_token_outside_the_one_put_parameter_call() -> None:
    """The raw token, and its base64 basic-auth form, must appear in exactly one recorded call's
    args: the `aws ssm put-parameter` that stores it (Global Constraints: the token appears on a
    command line only in that single call)."""
    token = "glc_super_secret_token"
    run = FakeRun().on("aws", "ssm", "put-parameter")

    cli.store_grafana_token(run, {}, "dev", ask_id=lambda prompt: "123456", ask_secret=lambda prompt: token)

    b64_value = base64.b64encode(f"123456:{token}".encode()).decode()
    put_calls = run.called("aws", "ssm", "put-parameter")
    assert len(put_calls) == 1
    assert b64_value in put_calls[0].args
    for call in run.calls:
        for arg in call.args:
            assert token not in arg
            if call is not put_calls[0]:
                assert b64_value not in arg


def test_deploy_never_puts_the_grafana_token_in_any_call_args(stage_dir: Path) -> None:
    """The Grafana token (here SSM's raw parameter value "dG9rZW4=") reaches Terraform only as
    the TF_VAR_grafana_otlp_auth environment variable (stage_env), never as a command argument,
    for every call a full deploy makes."""
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    token = "dG9rZW4="
    for call in run.calls:
        assert all(token not in arg for arg in call.args)


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


def test_deploy_migrates_the_database_before_terraform_applies_the_new_code(stage_dir: Path) -> None:
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    [migrate] = run.called(sys.executable, "-m", "alembic")
    assert migrate.args[-2:] == ["upgrade", "head"]
    assert migrate.env is not None
    assert migrate.env["NETTRIAGE_MIGRATION_DATABASE_URL"] == OWNER_URL
    assert run.first(sys.executable) < run.first("terraform", "init") < run.first("terraform", "apply")


def test_deploy_syncs_reference_data_after_migrating_and_before_terraform(
    stage_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    [sync] = run.called(sys.executable, "-m", "nettriage.adapters.reference_data")
    assert sync.env is not None
    assert sync.env["NETTRIAGE_MIGRATION_DATABASE_URL"] == OWNER_URL
    assert set(sync.redact) == {OWNER_URL, "owner-s3cret"}
    assert (
        run.first(sys.executable, "-m", "alembic")
        < run.first(sys.executable, "-m", "nettriage.adapters.reference_data")
        < run.first("terraform", "init")
    )
    assert SYNCED.strip() in capsys.readouterr().out


def test_a_failed_reference_sync_stops_the_deploy_before_terraform(stage_dir: Path) -> None:
    run = deployable()
    run.rules.insert(
        0,
        (
            (sys.executable, "-m", "nettriage.adapters.reference_data"),
            CommandError("`python -m` failed with exit code 1: Error: bad data"),
        ),
    )

    with pytest.raises(CommandError, match="Syncing reference data failed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called("terraform") == []


def test_deploy_never_puts_the_database_owner_url_in_any_call_args(stage_dir: Path) -> None:
    run = deployable()

    cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    for call in run.calls:
        assert all("owner-s3cret" not in arg for arg in call.args)


def test_deploy_stops_before_terraform_without_a_stored_database_url(stage_dir: Path) -> None:
    run = deployable()
    run.rules.insert(
        0,
        (
            ("aws", "ssm", "get-parameter", "--name", "/nettriage/dev/db/owner-url"),
            CommandError("ParameterNotFound"),
        ),
    )

    with pytest.raises(CommandError, match="just store-database-url dev"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called(sys.executable) == []
    assert run.called("terraform", "apply") == []


def test_a_failed_migration_stops_the_deploy_before_terraform(stage_dir: Path) -> None:
    run = deployable()
    run.rules.insert(0, ((sys.executable,), CommandError("`alembic upgrade` failed with exit code 1.")))

    with pytest.raises(CommandError, match="Database migrations failed; nothing was deployed"):
        cli.deploy(run, {}, "dev", smoke_main=lambda argv: 0)

    assert run.called("terraform") == []


def test_store_database_url_checks_and_stores_it_without_printing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = FakeRun().on("aws", "ssm", "put-parameter")

    cli.store_database_url(run, {}, "dev", ask_secret=lambda prompt: f"  {OWNER_URL}  ")

    [put] = run.calls
    assert put.args[put.args.index("--name") + 1] == "/nettriage/dev/db/owner-url"
    assert put.args[put.args.index("--value") + 1] == OWNER_URL
    output = capsys.readouterr()
    assert "Stored /nettriage/dev/db/owner-url as a SecureString." in output.out
    assert "owner-s3cret" not in output.out + output.err


def test_store_database_url_refuses_a_pooled_string_and_stores_nothing() -> None:
    run = FakeRun()
    pooled = OWNER_URL.replace("ep-quiet-sun-123456.", "ep-quiet-sun-123456-pooler.")

    with pytest.raises(CommandError, match="direct"):
        cli.store_database_url(run, {}, "dev", ask_secret=lambda prompt: pooled)

    assert run.calls == []
