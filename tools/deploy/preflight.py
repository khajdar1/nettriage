"""Read-only checks that the account still allows what a deploy needs (spec Revision 2, D9)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.deploy.config import REGION, otlp_auth_parameter, state_bucket
from tools.deploy.runner import CommandError, Runner

TFVAR = re.compile(r'^\s*(\w+)\s*=\s*"([^"]*)"\s*$', re.MULTILINE)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def read_tfvars(path: Path) -> dict[str, str]:
    return dict(TFVAR.findall(path.read_text(encoding="utf-8"))) if path.exists() else {}


def _probe(run: Runner, env: Mapping[str, str], name: str, args: Sequence[str], hint: str) -> Check:
    try:
        run(args, env=env)
    except CommandError as exc:
        return Check(name, False, f"{hint} ({exc})")
    return Check(name, True)


def account_checks(run: Runner, env: Mapping[str, str], account_id: str) -> list[Check]:
    denied = "AWS denied this call; the account's policies may have changed (see the runbook)"
    return [
        _probe(run, env, f"Lambda in {REGION}", ["aws", "lambda", "list-functions", "--region", REGION, "--max-items", "1"], denied),
        _probe(run, env, "IAM", ["aws", "iam", "list-roles", "--max-items", "1"], denied),
        _probe(run, env, "CloudFront", ["aws", "cloudfront", "list-distributions", "--max-items", "1"], denied),
        _probe(run, env, "Budgets", ["aws", "budgets", "describe-budgets", "--account-id", account_id, "--max-results", "1"], denied),
        _probe(run, env, f"SSM in {REGION}", ["aws", "ssm", "describe-parameters", "--region", REGION, "--max-results", "1"], denied),
    ]


def layer_check(run: Runner, env: Mapping[str, str], name: str, arn: str) -> Check:
    if f":{REGION}:" not in arn:
        return Check(name, False, f"'{arn}' isn't a {REGION} layer ARN; fix it in terraform.tfvars")
    try:
        info = json.loads(
            run(["aws", "lambda", "get-layer-version-by-arn", "--arn", arn, "--region", REGION], env=env).stdout
        )
    except CommandError as exc:
        return Check(name, False, f"not found or not shared; check the layer's current version ({exc})")
    architectures = info.get("CompatibleArchitectures") or ["arm64", "x86_64"]  # none listed = any
    return Check(name, "arm64" in architectures, f"architectures: {', '.join(architectures)}")


def parameter_check(run: Runner, env: Mapping[str, str], stage: str) -> Check:
    name = otlp_auth_parameter(stage)
    label = "Grafana token in SSM"
    try:
        found = run(
            ["aws", "ssm", "describe-parameters", "--parameter-filters", f"Key=Name,Values={name}",
             "--query", "Parameters[0].Name", "--output", "text"],
            env=env,
        ).stdout.strip()
    except CommandError as exc:
        return Check(label, False, str(exc))
    if found != name:
        return Check(label, False, f"missing; run: just store-grafana-token {stage}")
    return Check(label, True)


def endpoint_check(tfvars: Mapping[str, str]) -> Check:
    ok = tfvars.get("grafana_otlp_endpoint", "").startswith("https://")
    detail = "" if ok else "set grafana_otlp_endpoint in terraform.tfvars to your stack's https OTLP endpoint"
    return Check("Grafana OTLP endpoint in terraform.tfvars", ok, detail)


def stage_checks(
    run: Runner, env: Mapping[str, str], stage: str, account_id: str, tfvars: Mapping[str, str]
) -> list[Check]:
    bucket = state_bucket(account_id)
    return [
        _probe(run, env, "Terraform state bucket", ["aws", "s3api", "head-bucket", "--bucket", bucket],
               "missing; run: just bootstrap <your-email>"),
        parameter_check(run, env, stage),
        endpoint_check(tfvars),
        layer_check(run, env, "Lambda Web Adapter layer", tfvars.get("lwa_layer_arn", "")),
        layer_check(run, env, "OpenTelemetry collector layer", tfvars.get("otel_collector_layer_arn", "")),
    ]


def report(checks: list[Check]) -> bool:
    for check in checks:
        line = f"{'PASS' if check.ok else 'FAIL'}  {check.name}"
        print(f"{line}  {check.detail}" if check.detail else line)
    return all(check.ok for check in checks)
