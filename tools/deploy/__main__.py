"""Owner-run infrastructure commands (spec §11.5, ADR 0013).

  python -m tools.deploy preflight [--stage dev]
  python -m tools.deploy bootstrap --budget-email you@example.com [--anomaly-monitor-arn ARN]
  python -m tools.deploy store-grafana-token [--stage dev]
  python -m tools.deploy plan [--stage dev] [--no-comment]
  python -m tools.deploy deploy [--stage dev]

Every command uses the owner's short-lived `aws login` session: profile "nettriage", or
$NETTRIAGE_AWS_PROFILE, or --profile before the command name.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from tools import smoke
from tools.deploy import config, github, gitguards, preflight, publish, secrets, session, terraform
from tools.deploy import runner
from tools.deploy.runner import CommandError, Runner

SmokeMain = Callable[[list[str]], int]


def run_preflight(run: Runner, env: Mapping[str, str], stage: str | None) -> bool:
    account = session.account_id(run, env)
    print(f"AWS account {account}, region {config.REGION}")
    checks = preflight.account_checks(run, env, account)
    if stage is not None:
        tfvars = preflight.read_tfvars(config.stage_dir(stage) / "terraform.tfvars")
        checks += preflight.stage_checks(run, env, stage, account, tfvars)
    return preflight.report(checks)


def stage_env(run: Runner, env: Mapping[str, str], stage: str, sha: str, lambda_zip: Path) -> dict[str, str]:
    """Terraform's inputs for a stage, passed as environment variables, never as arguments."""
    return {
        **env,
        "TF_VAR_lambda_zip_path": str(lambda_zip),
        "TF_VAR_app_version": sha,
        "TF_VAR_grafana_otlp_auth": secrets.read_otlp_auth(run, env, stage),
    }


def bootstrap(run: Runner, env: Mapping[str, str], budget_email: str, anomaly_monitor_arn: str) -> None:
    recovered = config.BOOTSTRAP_DIR / "terraform.tfstate.recovered"
    if recovered.exists():
        raise CommandError(
            f"A saved bootstrap state exists at {recovered}. Don't bootstrap again; "
            "ask for help to push it into the state bucket."
        )
    if not run_preflight(run, env, stage=None):
        raise CommandError("Preflight failed; nothing was created.")
    bucket = config.state_bucket(session.account_id(run, env))
    variables = [f"-var=budget_email={budget_email}", f"-var=anomaly_monitor_arn={anomaly_monitor_arn}"]
    if run(["aws", "s3api", "head-bucket", "--bucket", bucket], env=env, check=False).returncode == 0:
        terraform.init(run, env, config.BOOTSTRAP_DIR, bucket, config.BOOTSTRAP_STATE_KEY)
        terraform.apply(run, env, config.BOOTSTRAP_DIR, variables)
        return
    # First run: the state bucket doesn't exist yet. Apply with local state in a scratch copy
    # (without backend.tf), then push that state into the bucket the apply just created.
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch) / "bootstrap"
        shutil.copytree(
            config.BOOTSTRAP_DIR, work,
            ignore=shutil.ignore_patterns("backend.tf", ".terraform", "tests", "*.tfstate*"),
        )
        local_state = work / "terraform.tfstate"
        run(["terraform", "init", "-input=false"], env=env, cwd=work)
        try:
            terraform.apply(run, env, work, variables)
            terraform.init(run, env, config.BOOTSTRAP_DIR, bucket, config.BOOTSTRAP_STATE_KEY)
            run(["terraform", "state", "push", str(local_state)], env=env, cwd=config.BOOTSTRAP_DIR)
        except BaseException as exc:
            # Ctrl+C (KeyboardInterrupt) counts too: never lose state with the scratch directory.
            if not local_state.exists():
                raise
            shutil.copy2(local_state, recovered)
            raise CommandError(
                f"The first bootstrap didn't finish; its state is saved in {recovered} (git-ignored). "
                "Keep that file and ask for help before retrying."
            ) from exc
    print(f"Bootstrap state is now in s3://{bucket}/{config.BOOTSTRAP_STATE_KEY}")


def plan_comment(stage: str, sha: str, changes: list[str]) -> str:
    lines = [f"### Terraform plan: {stage} ({sha[:7]})", ""]
    lines += ["```", *changes, "```"] if changes else ["No changes."]
    return "\n".join(lines) + "\n"


def plan(run: Runner, env: Mapping[str, str], stage: str, post_comment: bool) -> list[str]:
    sha = gitguards.head_sha(run)
    ci = github.latest_run(run, config.CI_WORKFLOW, sha)
    if ci is None or ci.status != "completed":
        raise CommandError(f"CI hasn't finished for {sha[:7]}. Push the branch, wait for the ci workflow, then plan again.")
    bucket = config.state_bucket(session.account_id(run, env))
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory() as scratch:
        dist = github.download(run, ci.run_id, config.BACKEND_ARTIFACT, Path(scratch) / "dist")
        tf_env = stage_env(run, env, stage, sha, dist / "backend.zip")
        terraform.init(run, tf_env, workdir, bucket, config.state_key(stage))
        changes = terraform.plan(run, tf_env, workdir, Path(scratch) / "tfplan")
        body = plan_comment(stage, sha, changes)
        print(body)
        if post_comment:
            comment_file = Path(scratch) / "plan.md"
            comment_file.write_text(body, encoding="utf-8")
            github.comment_on_pr(run, comment_file)
    return changes


def deploy(run: Runner, env: Mapping[str, str], stage: str, smoke_main: SmokeMain = smoke.main) -> None:
    if not run_preflight(run, env, stage):
        raise CommandError("Preflight failed; nothing was deployed.")
    sha = gitguards.require_clean_main(run)
    ci = github.require_success(run, config.CI_WORKFLOW, sha, event="push")
    github.require_success(run, config.CODEQL_WORKFLOW, sha, event="push")
    bucket = config.state_bucket(session.account_id(run, env))
    workdir = config.stage_dir(stage)
    with tempfile.TemporaryDirectory() as scratch:
        dist = github.download(run, ci.run_id, config.BACKEND_ARTIFACT, Path(scratch) / "dist")
        web = github.download(run, ci.run_id, config.WEB_ARTIFACT, Path(scratch) / "web")
        tf_env = stage_env(run, env, stage, sha, dist / "backend.zip")
        terraform.init(run, tf_env, workdir, bucket, config.state_key(stage))
        terraform.apply(run, tf_env, workdir)
        outputs = terraform.outputs(run, tf_env, workdir)
        publish.publish_web(run, env, web, outputs["web_bucket"], outputs["distribution_id"])
    code = smoke_main(
        ["--base-url", f"https://{outputs['cloudfront_domain']}",
         "--function-url", outputs["function_url"],
         "--version", sha]
    )
    if code != 0:
        raise CommandError(
            "Smoke tests failed (see the FAIL lines above). To roll back, revert the PR on GitHub, "
            "then deploy main again."
        )
    print(f"Deployed {sha[:7]} to {stage}: https://{outputs['cloudfront_domain']}")


def store_grafana_token(
    run: Runner,
    env: Mapping[str, str],
    stage: str,
    ask_id: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
) -> None:
    instance_id = ask_id("Grafana Cloud OTLP instance ID (a number): ")
    token = ask_secret("Grafana Cloud token with metrics:write, logs:write, traces:write (hidden): ")
    secrets.store_otlp_auth(run, env, stage, secrets.otlp_auth_value(instance_id, token))
    print(f"Stored {config.otlp_auth_parameter(stage)} as a SecureString.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.deploy", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default=os.environ.get("NETTRIAGE_AWS_PROFILE", config.DEFAULT_PROFILE))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "store-grafana-token", "plan", "deploy"):
        command = commands.add_parser(name)
        command.add_argument("--stage", choices=config.STAGES, default="dev")
    commands.choices["plan"].add_argument("--no-comment", action="store_true")
    boot = commands.add_parser("bootstrap")
    boot.add_argument("--budget-email", required=True)
    boot.add_argument("--anomaly-monitor-arn", default="")
    return parser


def main(argv: list[str] | None = None, run: Runner = runner.run) -> int:
    args = build_parser().parse_args(argv)
    try:
        env = session.aws_env(run, args.profile)
        config.PLUGIN_CACHE.mkdir(parents=True, exist_ok=True)
        env.setdefault("TF_PLUGIN_CACHE_DIR", str(config.PLUGIN_CACHE))
        if args.command == "preflight":
            return 0 if run_preflight(run, env, args.stage) else 1
        if args.command == "bootstrap":
            bootstrap(run, env, args.budget_email, args.anomaly_monitor_arn)
        elif args.command == "store-grafana-token":
            store_grafana_token(run, env, args.stage)
        elif args.command == "plan":
            plan(run, env, args.stage, post_comment=not args.no_comment)
        else:
            deploy(run, env, args.stage)
    except CommandError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
