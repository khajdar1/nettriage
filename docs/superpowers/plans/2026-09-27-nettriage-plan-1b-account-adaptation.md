# NetTriage Plan 1b: Account Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Plan 1's walking skeleton deployable on the owner's actual AWS account at $0. Regional resources move to eu-north-1, CI keeps every check but holds no AWS access, and the owner plans and deploys CI-built artifacts from their machine with a short-lived `aws login` session.

**Architecture:** Terraform moves to eu-north-1 and drops the GitHub OIDC provider, CI roles and permissions boundary. The bootstrap keeps its state bucket and budget alerts. A new `tools/deploy` package runs `preflight`, `bootstrap`, `store-grafana-token`, `plan` and `deploy` through thin `just` recipes. It talks to AWS with the AWS CLI, using credentials exported from the owner's `aws login` session, to GitHub with the `gh` CLI, and to Terraform directly. All external commands go through one injectable runner, so every guard is unit-tested with a fake. A CI check keeps workflows and Terraform free of cloud access.

**Tech Stack:** Python 3.14 (stdlib only, plus `httpx` via `tools/smoke.py`), pytest, Terraform ≥ 1.11 with the AWS provider `~> 6.0`, AWS CLI v2 (v2.32 or later, for `aws login`), GitHub CLI, `just`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, revision 2. Start with its "Revision 2 (2026-09-27)" section (R1–R5, D1–D9), then read §3.4, §6.7, §6.8, §11.5 and §11.7. This plan argues from those sections; where they conflict with Plan 1, they win.

**Where this runs:** branch `plan-1/walking-skeleton` in `C:\dev\nettriage`, which is draft PR #1 (https://github.com/khajdar1/nettriage/pull/1). It builds on Plan 1 (`docs/superpowers/plans/2026-09-26-nettriage-plan-1-walking-skeleton.md`, Tasks 1–13, all complete).

## Global Constraints

- **Region.** Regional resources live in **`eu-north-1`**. The Terraform AWS provider region is `eu-north-1` in every stack. The state bucket is `nettriage-tfstate-<account-id>` in `eu-north-1`, and the S3 backend uses `use_lockfile = true`.
- **CI holds no cloud access.** No workflow has `id-token: write`, uses an `aws-actions/*` action, or references `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` or `role-to-assume`. No Terraform defines `aws_iam_openid_connect_provider` or trusts `token.actions.githubusercontent.com`.
- **The owner is the only identity that changes AWS.** Changes use the owner's `aws login` session: profile `nettriage` by default, overridable with `NETTRIAGE_AWS_PROFILE`. A profile without a session token, meaning long-lived access keys, is refused.
- **What may be deployed.** A deploy takes only the CI-built artifacts (`backend-zip`, `web-dist`) of a commit that meets all of these:
  - it is checked out as a clean `main`;
  - it equals `origin/main`;
  - its `ci.yml` and `codeql.yml` runs for the `push` event concluded `success`.

  `preflight` runs first. `terraform apply` is confirmed by the owner interactively, and the smoke tests run after.
- **The Grafana token.** It is the SSM SecureString `/nettriage/<stage>/grafana-otlp-auth`.
  - It is never printed and never written to disk.
  - It never appears in error messages or PR comments.
  - It appears on a command line only in the single `aws ssm put-parameter` call.
  - Terraform receives it as `TF_VAR_grafana_otlp_auth` in the environment.
- **Unchanged from Plan 1:**
  - names `nettriage-<stage>-<name>`;
  - default tags `Project=nettriage`, `Env=<stage>`, `ManagedBy=terraform`;
  - the `api` Lambda: 1024 MB, 29 s, `arm64`, `python3.14`, and an `AWS_IAM`/`BUFFERED` Function URL that only the CloudFront distribution may invoke;
  - CloudWatch log retention of 7 days;
  - the exact CSP;
  - Terraform ≥ 1.11 and AWS provider `~> 6.0`;
  - every action pinned to a full commit SHA, with least-privilege `permissions:`.
- **This Windows machine.** Run Python tools as modules (`uv run --project backend python -m …`); host policy blocks some uv console-script launchers (ruling R9). CI workflow commands stay as they are.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Work test-first (TDD).

## Review Focus

1. **An expired or missing `aws login` session.** Every command stops with `STOP: … aws login --profile nettriage` instead of a stack trace. Test: Task 4, `test_missing_session_says_how_to_sign_in`.
2. **A deploy of anything but a clean, pushed, CI-green and CodeQL-green `main`.** It is refused before any Terraform, S3 or CloudFront call. Tests: Task 5 `test_anything_else_is_refused` and Task 7 `test_nothing_changes_in_aws_unless_ci_is_green`.
3. **The Grafana token leaking into output, errors, PR comments or files.** This must never happen. Tests: Task 4 `test_failure_names_only_the_program_and_subcommand`, and Task 7 `test_store_grafana_token_prompts_and_never_prints_the_token` and `test_plan_posts_addresses_only_to_the_pr`.
4. **CI artifacts expired, since they're kept 7 days.** The message says to re-run the workflow. Test: Task 5, `test_expired_artifacts_are_explained`.
5. **The first bootstrap apply failing midway.** The partial local state is saved in the repo (git-ignored) and not lost with the scratch directory. Test: Task 7, `test_a_failed_first_apply_keeps_its_partial_state`.

---

### Task 1: Bootstrap stack for eu-north-1 without CI identities

**Files:**
- Modify: `infra/bootstrap/variables.tf`, `infra/bootstrap/state.tf`, `infra/bootstrap/outputs.tf`
- Delete: `infra/bootstrap/oidc.tf`, `infra/bootstrap/boundary.tf`
- Create: `infra/bootstrap/backend.tf`
- Test: `infra/bootstrap/tests/bootstrap.tftest.hcl` (replaced)

**Interfaces:**
- Produces:
  - Bootstrap variables `aws_region` (default `"eu-north-1"`), `budget_email` and `anomaly_monitor_arn` (default `""`).
  - One output, `state_bucket`.
  - A partial S3 backend (`backend "s3" {}`) whose bucket, key (`bootstrap/terraform.tfstate`) and region Task 7's `bootstrap` command passes in.

- [ ] **Step 1: Write the failing test**

Replace `infra/bootstrap/tests/bootstrap.tftest.hcl` with:
```hcl
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  budget_email = "owner@example.com"
}

run "defaults_to_the_accounts_region" {
  command = plan

  assert {
    condition     = var.aws_region == "eu-north-1"
    error_message = "Regional resources live in eu-north-1, the account's only Region (spec Revision 2, R1)."
  }
}

run "state_bucket_is_private_versioned_encrypted_and_tls_only" {
  command = apply

  assert {
    condition     = aws_s3_bucket.state.bucket == "nettriage-tfstate-123456789012"
    error_message = "The state bucket name must include the account ID."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_policy && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "The state bucket must block public access."
  }
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State history must be versioned."
  }
  assert {
    condition     = strcontains(aws_s3_bucket_policy.state.policy, "aws:SecureTransport")
    error_message = "The state bucket must deny non-TLS requests."
  }
}

run "budget_alerts_at_one_and_three_dollars" {
  command = apply

  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 4
    error_message = "Expected alerts at $1 and $3, actual and forecast (spec §6.7)."
  }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run (Git Bash, from the repo root): `export TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"; cd infra/bootstrap && terraform init -backend=false -input=false >/dev/null && terraform test`
Expected: FAIL. `github_owner` has no value, and `defaults_to_the_accounts_region` fails because the default is `us-east-1`.

- [ ] **Step 3: Implement**

Delete `infra/bootstrap/oidc.tf` and `infra/bootstrap/boundary.tf` (`git rm`).

Replace `infra/bootstrap/variables.tf` with:
```hcl
variable "aws_region" {
  type        = string
  default     = "eu-north-1"
  description = "The account's only Region for regional resources (spec Revision 2, R1)."
}

variable "budget_email" {
  type        = string
  description = "Email address for budget and cost anomaly alerts."
}

variable "anomaly_monitor_arn" {
  type        = string
  default     = ""
  description = "ARN of the account's existing Cost Anomaly Detection services monitor. Empty = no subscription."
}
```

In `infra/bootstrap/state.tf`, replace the `locals` block (lines 3–6) with:
```hcl
locals {
  account_id = data.aws_caller_identity.current.account_id
}
```

Replace `infra/bootstrap/outputs.tf` with:
```hcl
output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}
```

Create `infra/bootstrap/backend.tf`:
```hcl
# The bootstrap's own state lives in the bucket it creates. `just bootstrap` applies the first
# run with local state, then pushes it here; bucket, key and region come from tools/deploy.
terraform {
  backend "s3" {}
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `infra/bootstrap`): `terraform fmt -check && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test`
Expected: `Success! The configuration is valid.` and `Success! 3 passed, 0 failed.` with no warnings. `terraform test` doesn't use the configured backend. If it refuses to run because of `backend.tf`, stop and report BLOCKED with the output.

- [ ] **Step 5: Commit**

```bash
git add -A infra/bootstrap
git commit -m "feat(infra): eu-north-1 bootstrap with only state and budgets

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: App module, dev stage and edge tests in eu-north-1

**Files:**
- Modify: `infra/modules/app/variables.tf`, `infra/modules/app/main.tf`, `infra/modules/app/tests/app.tftest.hcl`
- Modify: `infra/envs/dev/versions.tf`, `infra/envs/dev/main.tf`, `infra/envs/dev/terraform.tfvars`
- Modify: `infra/modules/edge/tests/edge.tftest.hcl`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `module "app"` loses its `permissions_boundary_arn` input.
  - `lwa_layer_arn` and `otel_collector_layer_arn` must now be `eu-north-1` ARNs.
  - The dev stage's provider region is `eu-north-1`.
  - `infra/envs/dev/terraform.tfvars` keeps the three keys `lwa_layer_arn`, `otel_collector_layer_arn` and `grafana_otlp_endpoint`. Task 6's preflight reads them.

- [ ] **Step 1: Write the failing tests**

Replace `infra/modules/app/tests/app.tftest.hcl` with:
```hcl
mock_provider "aws" {
  # The default mock for a computed "arn" attribute is a short random string, not
  # ARN-shaped. aws_lambda_function.role validates its value looks like an ARN,
  # so give aws_iam_role.api's computed arn a realistic value.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/nettriage-dev-api"
    }
  }
}

variables {
  stage                    = "dev"
  lambda_zip_path          = "tests/fixtures/app.zip"
  app_version              = "test-sha"
  lwa_layer_arn            = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
  otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
  grafana_otlp_endpoint    = "https://otlp-gateway.example.grafana.net/otlp"
  grafana_otlp_auth        = "dGVzdDp0ZXN0"
}

run "function_is_arm64_python_behind_iam_auth" {
  command = apply

  assert {
    condition     = aws_lambda_function.api.function_name == "nettriage-dev-api"
    error_message = "Names follow nettriage-<stage>-<name>."
  }
  assert {
    condition     = aws_lambda_function.api.runtime == "python3.14" && aws_lambda_function.api.architectures == tolist(["arm64"])
    error_message = "The API runs on python3.14, arm64."
  }
  assert {
    condition     = aws_lambda_function.api.memory_size == 1024 && aws_lambda_function.api.timeout == 29
    error_message = "The API uses 1024 MB and a 29 s timeout (spec §3.5)."
  }
  assert {
    condition     = aws_lambda_function_url.api.authorization_type == "AWS_IAM" && aws_lambda_function_url.api.invoke_mode == "BUFFERED"
    error_message = "The Function URL requires IAM auth (only CloudFront signs requests)."
  }
  assert {
    condition     = aws_lambda_function.api.environment[0].variables["AWS_LAMBDA_EXEC_WRAPPER"] == "/opt/bootstrap"
    error_message = "The Lambda Web Adapter must wrap the runtime."
  }
}

run "logs_are_kept_seven_days" {
  command = apply

  assert {
    condition     = aws_cloudwatch_log_group.api.retention_in_days == 7
    error_message = "Log retention is 7 days (spec §9.3)."
  }
}

run "rejects_an_x86_adapter_layer" {
  command = plan

  variables {
    lwa_layer_arn = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerX86:30"
  }

  expect_failures = [var.lwa_layer_arn]
}

run "rejects_a_layer_from_another_region" {
  command = plan

  variables {
    lwa_layer_arn = "arn:aws:lambda:us-east-1:753240598075:layer:LambdaAdapterLayerArm64:30"
  }

  expect_failures = [var.lwa_layer_arn]
}

run "rejects_a_non_https_otlp_endpoint" {
  command = plan

  variables {
    grafana_otlp_endpoint = "http://example.com/otlp"
  }

  expect_failures = [var.grafana_otlp_endpoint]
}
```

In `infra/modules/edge/tests/edge.tftest.hcl`, replace every `us-east-1` with `eu-north-1`. There are three occurrences, on lines 21, 37 and 41. This changes test inputs only; the edge module has no region logic.

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `infra/modules/app`): `terraform init -backend=false -input=false >/dev/null && terraform test`
Expected: FAIL. The eu-north-1 layer ARNs fail the old `us-east-1` validations, `permissions_boundary_arn` has no value, and `rejects_a_layer_from_another_region` fails because a us-east-1 ARN is still accepted.

- [ ] **Step 3: Implement**

In `infra/modules/app/variables.tf`:
- Replace the `lwa_layer_arn` validation with:
  ```hcl
    validation {
      condition     = can(regex("^arn:aws:lambda:eu-north-1:[0-9]{12}:layer:LambdaAdapterLayerArm64:[0-9]+$", var.lwa_layer_arn))
      error_message = "Use the eu-north-1 arm64 Lambda Web Adapter layer (LambdaAdapterLayerArm64)."
    }
  ```
- Replace the `otel_collector_layer_arn` validation with:
  ```hcl
    validation {
      condition     = can(regex("^arn:aws:lambda:eu-north-1:[0-9]{12}:layer:opentelemetry-collector-arm64-[0-9a-z_-]+:[0-9]+$", var.otel_collector_layer_arn))
      error_message = "Use the eu-north-1 arm64 OpenTelemetry collector layer."
    }
  ```
- Delete the whole `variable "permissions_boundary_arn" { … }` block (the last 4 lines of the file).

In `infra/modules/app/main.tf`, delete the line `  permissions_boundary = var.permissions_boundary_arn` from `aws_iam_role.api`, then run `terraform fmt` so `name` realigns.

In `infra/envs/dev/versions.tf`:
- Change `region = "us-east-1"` to `region = "eu-north-1"`.
- Change the backend comment to:
  `# bucket, key, region and use_lockfile are passed with -backend-config by tools/deploy.`

In `infra/envs/dev/main.tf`:
- Delete line 1 (`data "aws_caller_identity" "current" {}`) and the blank line after it.
- Delete the two lines in `module "app"` that set `permissions_boundary_arn` and its comment.

Replace `infra/envs/dev/terraform.tfvars` with:
```hcl
# Non-secret dev settings, reviewed like code. `just preflight` checks both layer ARNs exist in
# eu-north-1 and support arm64; if one is missing, use the current version from the layer's
# release notes (Lambda Web Adapter README; opentelemetry-lambda "layer-collector" releases).
lwa_layer_arn            = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
grafana_otlp_endpoint    = "<your Grafana Cloud OTLP endpoint, e.g. https://otlp-gateway-prod-eu-north-0.grafana.net/otlp>"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from the repo root): `export TF_PLUGIN_CACHE_DIR="$HOME/.terraform.d/plugin-cache"; just tf-check && (cd infra/envs/dev && terraform init -backend=false -input=false >/dev/null && terraform validate)`
Expected:
- `just tf-check` passes with bootstrap 3 passed, app 5 passed and edge 4 passed.
- `infra/envs/dev` prints `Success! The configuration is valid.`
- There are no warnings.

- [ ] **Step 5: Commit**

```bash
git add infra/modules/app infra/envs/dev infra/modules/edge/tests
git commit -m "feat(infra): run the dev stage in eu-north-1 without a permissions boundary

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: CI without cloud access, enforced

**Files:**
- Create: `tools/check_no_cloud_access.py`
- Test: `tools/tests/test_check_no_cloud_access.py`
- Modify: `.github/workflows/ci.yml` (delete the `plan-dev` and `deploy-dev` jobs; add one step to `package`)
- Modify: `justfile` (add `cloud-check`)

**Interfaces:**
- Produces:
  - `tools.check_no_cloud_access`, which exposes `WORKFLOW_RULES: dict[str, re.Pattern[str]]`, `TERRAFORM_RULES: dict[str, re.Pattern[str]]`, `problems_in(text: str, rules: dict[str, re.Pattern[str]]) -> list[str]` and `main(argv: list[str] | None = None) -> int`.
  - The CLI takes a repository root, `.` by default.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_check_no_cloud_access.py`:
```python
from pathlib import Path

import pytest

from tools.check_no_cloud_access import TERRAFORM_RULES, WORKFLOW_RULES, main, problems_in

SHA = "a" * 40


def test_a_checks_only_workflow_is_clean() -> None:
    text = f"permissions:\n  contents: read\njobs:\n  b:\n    steps:\n      - uses: actions/checkout@{SHA}\n"
    assert problems_in(text, WORKFLOW_RULES) == []


def test_requesting_an_oidc_token_is_flagged() -> None:
    text = "    permissions:\n      id-token: write\n"
    assert problems_in(text, WORKFLOW_RULES) == ["requests an OIDC token (id-token: write)"]


def test_aws_actions_are_flagged() -> None:
    text = f"      - uses: aws-actions/configure-aws-credentials@{SHA}\n"
    assert "uses an AWS action" in problems_in(text, WORKFLOW_RULES)


def test_aws_credentials_are_flagged() -> None:
    text = "        env:\n          AWS_SECRET_ACCESS_KEY: ${{ secrets.KEY }}\n"
    assert problems_in(text, WORKFLOW_RULES) == ["references AWS credentials"]


def test_a_github_oidc_trust_in_terraform_is_flagged() -> None:
    text = (
        'resource "aws_iam_openid_connect_provider" "github" {\n'
        '  url = "https://token.actions.githubusercontent.com"\n}\n'
    )
    assert problems_in(text, TERRAFORM_RULES) == [
        "defines an OIDC identity provider",
        "trusts GitHub's OIDC issuer",
    ]


def _repo(root: Path, workflow: str, terraform: str) -> Path:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    (root / "infra" / "bootstrap").mkdir(parents=True)
    (root / "infra" / "bootstrap" / "main.tf").write_text(terraform, encoding="utf-8")
    return root


def test_main_passes_a_repo_without_cloud_access(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, "permissions:\n  contents: read\n", 'resource "aws_s3_bucket" "s" {}\n')
    assert main([str(root)]) == 0
    assert "CI holds no cloud access." in capsys.readouterr().out


def test_main_fails_and_names_the_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path, "permissions:\n  id-token: write\n", "")
    assert main([str(root)]) == 1
    assert "ci.yml: requests an OIDC token" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just tools-test`
Expected: `ModuleNotFoundError: No module named 'tools.check_no_cloud_access'`. The existing 23 tests still pass.

- [ ] **Step 3: Implement**

`tools/check_no_cloud_access.py`:
```python
"""Fail if CI could reach AWS: CI holds no cloud access (spec Revision 2, D3; ADR 0013).

Usage: python tools/check_no_cloud_access.py [repository root]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKFLOW_RULES: dict[str, re.Pattern[str]] = {
    "requests an OIDC token (id-token: write)": re.compile(r"^\s*id-token:\s*write\b", re.MULTILINE),
    "uses an AWS action": re.compile(r"uses:\s*['\"]?aws-actions/"),
    "references AWS credentials": re.compile(
        r"\bAWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\b|role-to-assume"
    ),
}
TERRAFORM_RULES: dict[str, re.Pattern[str]] = {
    "defines an OIDC identity provider": re.compile(r'resource\s+"aws_iam_openid_connect_provider"'),
    "trusts GitHub's OIDC issuer": re.compile(r"token\.actions\.githubusercontent\.com"),
}


def problems_in(text: str, rules: dict[str, re.Pattern[str]]) -> list[str]:
    return [label for label, pattern in rules.items() if pattern.search(text)]


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(".")
    found = [
        f"{path}: {problem}"
        for path in sorted((root / ".github" / "workflows").glob("*.y*ml"))
        for problem in problems_in(path.read_text(encoding="utf-8"), WORKFLOW_RULES)
    ]
    found += [
        f"{path}: {problem}"
        for path in sorted((root / "infra").rglob("*.tf"))
        if ".terraform" not in path.parts
        for problem in problems_in(path.read_text(encoding="utf-8"), TERRAFORM_RULES)
    ]
    if found:
        print("CI must hold no cloud access (ADR 0013):\n  " + "\n  ".join(found))
        return 1
    print("CI holds no cloud access.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just tools-test`
Expected: `30 passed` (23 existing + 7 new), no warnings.

- [ ] **Step 5: Remove the AWS jobs from CI and enforce the check**

In `.github/workflows/ci.yml`:
- Delete the whole `plan-dev:` job and the whole `deploy-dev:` job, from `  plan-dev:` through the end of the file. The file then ends with the `dependency-review` job.
- In the `package` job, insert this step right after the "Check that every action is pinned to a commit SHA" step:
  ```yaml
      - name: Check that CI holds no cloud access
        run: uv run --locked --project backend python tools/check_no_cloud_access.py .
  ```

Append to `justfile`:
```just

# CI hygiene: no workflow or Terraform gives CI access to AWS (ADR 0013)
cloud-check:
    uv run --project backend python tools/check_no_cloud_access.py .
```

Run:
- `just cloud-check`. Expected: `CI holds no cloud access.`
- `just pin-check`. Expected: `All actions in .github\workflows are pinned.`
- `uv run --with pyyaml python -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(sorted(d['jobs']))"`. Expected: `['backend', 'dependency-review', 'edge-functions', 'frontend', 'package', 'terraform']`.

If `just cloud-check` flags Terraform, Task 1's `oidc.tf` deletion is missing. Stop and report.

- [ ] **Step 6: Commit**

```bash
git add tools/check_no_cloud_access.py tools/tests/test_check_no_cloud_access.py .github/workflows/ci.yml justfile
git commit -m "ci: drop AWS jobs and enforce that CI holds no cloud access

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Deploy tool foundation: runner, config, session, secrets, Terraform helpers

**Files:**
- Create: `tools/deploy/__init__.py`, `tools/deploy/runner.py`, `tools/deploy/config.py`, `tools/deploy/session.py`, `tools/deploy/secrets.py`, `tools/deploy/terraform.py`
- Create: `tools/tests/deploy_fakes.py` (the test double shared by Tasks 4–7)
- Test: `tools/tests/test_deploy_runner.py`, `tools/tests/test_deploy_session.py`, `tools/tests/test_deploy_secrets.py`, `tools/tests/test_deploy_terraform.py`

**Interfaces:**
- Produces:
  - `tools.deploy.runner`:
    - `CommandError(Exception)`
    - `Result(returncode: int, stdout: str)` (frozen dataclass)
    - the `Runner` protocol: `(args: Sequence[str], *, env: Mapping[str, str] | None = None, cwd: Path | None = None, interactive: bool = False, check: bool = True) -> Result`
    - `run`, the real implementation of `Runner`
  - `tools.deploy.config`:
    - constants `REPO: Path`, `REGION = "eu-north-1"`, `DEFAULT_PROFILE = "nettriage"`, `STAGES = ("dev",)`, `CI_WORKFLOW = "ci.yml"`, `CODEQL_WORKFLOW = "codeql.yml"`, `BACKEND_ARTIFACT = "backend-zip"`, `WEB_ARTIFACT = "web-dist"`, `BOOTSTRAP_DIR: Path`, `BOOTSTRAP_STATE_KEY = "bootstrap/terraform.tfstate"`, `PLUGIN_CACHE: Path`
    - `state_bucket(account_id: str) -> str`, `state_key(stage: str) -> str`, `otlp_auth_parameter(stage: str) -> str`, `stage_dir(stage: str) -> Path`
  - `tools.deploy.session`: `aws_env(run: Runner, profile: str) -> dict[str, str]` and `account_id(run: Runner, env: Mapping[str, str]) -> str`.
  - `tools.deploy.secrets`: `read_otlp_auth(run, env, stage) -> str`, `store_otlp_auth(run, env, stage, value: str) -> None` and `otlp_auth_value(instance_id: str, token: str) -> str`.
  - `tools.deploy.terraform`:
    - `backend_args(bucket: str, key: str) -> list[str]`
    - `init(run, env, cwd: Path, bucket: str, key: str) -> None`
    - `planned_changes(plan_json: str) -> list[str]`
    - `plan(run, env, cwd: Path, plan_file: Path) -> list[str]`
    - `apply(run, env, cwd: Path, extra_args: Sequence[str] = ()) -> None`
    - `outputs(run, env, cwd: Path) -> dict[str, str]`
  - `tools.tests.deploy_fakes`: `Call`, `FakeRun` (with `.on(*prefix, returns=…)`, `.called(*prefix)` and `.first(*prefix)`), `SESSION`, `signed_in() -> FakeRun` and `runs(*items) -> str`.

- [ ] **Step 1: Write the shared fake and the failing tests**

`tools/tests/deploy_fakes.py`:
```python
"""A scripted stand-in for tools.deploy.runner.run, shared by the deploy tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tools.deploy.runner import CommandError, Result


@dataclass
class Call:
    args: list[str]
    env: Mapping[str, str] | None
    cwd: Path | None
    interactive: bool


# A rule's answer: stdout text; an exit code (for check=False probes); an exception to raise;
# or a function of the call that returns stdout (and may create files or raise).
Answer = str | int | Exception | Callable[[Call], str]


@dataclass
class FakeRun:
    """Answers each command with the first rule whose prefix matches, and records every call."""

    rules: list[tuple[tuple[str, ...], Answer]] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def on(self, *prefix: str, returns: Answer = "") -> FakeRun:
        self.rules.append((prefix, returns))
        return self

    def __call__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        interactive: bool = False,
        check: bool = True,
    ) -> Result:
        call = Call(list(args), env, cwd, interactive)
        self.calls.append(call)
        for prefix, answer in self.rules:
            if tuple(call.args[: len(prefix)]) != prefix:
                continue
            if isinstance(answer, Exception):
                raise answer
            if isinstance(answer, int):
                if check and answer != 0:
                    raise CommandError(f"`{' '.join(call.args[:2])}` failed with exit code {answer}.")
                return Result(answer, "")
            if callable(answer):
                return Result(0, answer(call))
            return Result(0, answer)
        raise AssertionError(f"unexpected command: {call.args}")

    def called(self, *prefix: str) -> list[Call]:
        return [call for call in self.calls if tuple(call.args[: len(prefix)]) == prefix]

    def first(self, *prefix: str) -> int:
        """Index of the first call starting with prefix, for ordering assertions."""
        return next(
            i for i, call in enumerate(self.calls) if tuple(call.args[: len(prefix)]) == prefix
        )


SESSION = json.dumps(
    {
        "Version": 1,
        "AccessKeyId": "ASIAEXAMPLE",
        "SecretAccessKey": "example-secret",
        "SessionToken": "example-session",
        "Expiration": "2026-09-27T20:00:00Z",
    }
)


def signed_in() -> FakeRun:
    """A runner whose owner is signed in to account 123456789012."""
    return (
        FakeRun()
        .on("aws", "configure", "export-credentials", returns=SESSION)
        .on("aws", "sts", "get-caller-identity", returns="123456789012\n")
    )


def runs(*items: tuple[int, str, str, str]) -> str:
    """`gh run list --json databaseId,status,conclusion,event` output, newest first."""
    return json.dumps(
        [{"databaseId": i, "status": s, "conclusion": c, "event": e} for i, s, c, e in items]
    )
```

`tools/tests/test_deploy_runner.py`:
```python
import sys

import pytest

from tools.deploy.runner import CommandError, run


def test_captures_stdout() -> None:
    assert run([sys.executable, "-c", "print('hello')"]).stdout.strip() == "hello"


def test_failure_names_only_the_program_and_subcommand() -> None:
    script = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"
    with pytest.raises(CommandError) as err:
        run([sys.executable, "-c", script, "super-secret-value"])
    message = str(err.value)
    assert "exit code 3" in message
    assert "boom" in message
    assert "super-secret-value" not in message


def test_unchecked_failure_returns_the_exit_code() -> None:
    assert run([sys.executable, "-c", "import sys; sys.exit(4)"], check=False).returncode == 4


def test_missing_program_is_explained() -> None:
    with pytest.raises(CommandError, match="isn't installed"):
        run(["nettriage-no-such-program"])
```

`tools/tests/test_deploy_session.py`:
```python
import pytest

from tools.deploy import session
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import SESSION, FakeRun


def test_session_credentials_and_stockholm_reach_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "someone-else")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIALONGLIVED")
    run = FakeRun().on("aws", "configure", "export-credentials", returns=SESSION)

    env = session.aws_env(run, "nettriage")

    assert run.calls[0].args == [
        "aws", "configure", "export-credentials", "--profile", "nettriage", "--format", "process",
    ]
    assert env["AWS_ACCESS_KEY_ID"] == "ASIAEXAMPLE"
    assert env["AWS_SESSION_TOKEN"] == "example-session"
    assert env["AWS_REGION"] == "eu-north-1"
    assert env["AWS_DEFAULT_REGION"] == "eu-north-1"
    assert "AWS_PROFILE" not in env


def test_long_lived_keys_are_refused() -> None:
    keys = '{"Version": 1, "AccessKeyId": "AKIA", "SecretAccessKey": "s"}'
    run = FakeRun().on("aws", "configure", "export-credentials", returns=keys)
    with pytest.raises(CommandError, match="long-lived"):
        session.aws_env(run, "nettriage")


def test_missing_session_says_how_to_sign_in() -> None:
    run = FakeRun().on("aws", "configure", "export-credentials", returns=CommandError("expired"))
    with pytest.raises(CommandError, match="aws login --profile nettriage"):
        session.aws_env(run, "nettriage")


def test_account_id_is_read_from_sts() -> None:
    run = FakeRun().on("aws", "sts", "get-caller-identity", returns="123456789012\n")
    assert session.account_id(run, {}) == "123456789012"
```

`tools/tests/test_deploy_secrets.py`:
```python
import base64

import pytest

from tools.deploy import secrets
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun

PARAMETER = "/nettriage/dev/grafana-otlp-auth"


def test_token_is_read_decrypted_from_the_stage_parameter() -> None:
    run = FakeRun().on("aws", "ssm", "get-parameter", returns="dG9rZW4=\n")
    assert secrets.read_otlp_auth(run, {}, "dev") == "dG9rZW4="
    assert run.calls[0].args == [
        "aws", "ssm", "get-parameter", "--name", PARAMETER, "--with-decryption",
        "--query", "Parameter.Value", "--output", "text",
    ]


def test_missing_parameter_says_how_to_store_it() -> None:
    run = FakeRun().on("aws", "ssm", "get-parameter", returns=CommandError("ParameterNotFound"))
    with pytest.raises(CommandError, match="just store-grafana-token dev"):
        secrets.read_otlp_auth(run, {}, "dev")


def test_token_is_stored_as_a_securestring() -> None:
    run = FakeRun().on("aws", "ssm", "put-parameter")
    secrets.store_otlp_auth(run, {}, "dev", "dG9rZW4=")
    args = run.calls[0].args
    assert args[:3] == ["aws", "ssm", "put-parameter"]
    assert args[args.index("--name") + 1] == PARAMETER
    assert args[args.index("--type") + 1] == "SecureString"
    assert "--overwrite" in args


def test_empty_value_is_never_stored() -> None:
    run = FakeRun()
    with pytest.raises(CommandError, match="empty"):
        secrets.store_otlp_auth(run, {}, "dev", "   ")
    assert run.calls == []


def test_otlp_auth_value_is_base64_of_instance_and_token() -> None:
    value = secrets.otlp_auth_value(" 123456 ", " glc_abc ")
    assert base64.b64decode(value).decode() == "123456:glc_abc"


@pytest.mark.parametrize(("instance_id", "token"), [("abc", "glc_x"), ("123456", "  "), ("", "glc_x")])
def test_bad_instance_or_empty_token_is_rejected(instance_id: str, token: str) -> None:
    with pytest.raises(CommandError):
        secrets.otlp_auth_value(instance_id, token)
```

`tools/tests/test_deploy_terraform.py`:
```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just tools-test`
Expected: collection errors, `ModuleNotFoundError: No module named 'tools.deploy'`.

- [ ] **Step 3: Implement**

`tools/deploy/__init__.py`:
```python
"""Owner-run infrastructure commands: preflight, bootstrap, plan and deploy (spec §11.5, ADR 0013)."""
```

`tools/deploy/runner.py`:
```python
"""The one place that starts external commands (aws, gh, git, terraform), so tests can swap in a fake."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class CommandError(Exception):
    """A step failed. The message says what happened and what to do next; it never contains secrets."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str


class Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        interactive: bool = False,
        check: bool = True,
    ) -> Result: ...


def run(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    interactive: bool = False,
    check: bool = True,
) -> Result:
    """Run a command. Interactive commands share the terminal (terraform apply asks for "yes").

    Error messages name only the program and its first argument, never the rest, because some
    arguments are secrets (the SSM parameter value).
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argument list, no shell
            list(args),
            env=dict(env) if env is not None else None,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            stdout=None if interactive else subprocess.PIPE,
            stderr=None if interactive else subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        raise CommandError(f"`{args[0]}` isn't installed or isn't on PATH.") from None
    if check and completed.returncode != 0:
        tail = "" if interactive else _last_line(completed.stderr)
        what = " ".join(args[:2])
        reason = f": {tail}" if tail else "."
        raise CommandError(f"`{what}` failed with exit code {completed.returncode}{reason}")
    return Result(completed.returncode, "" if interactive else completed.stdout)


def _last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
```

`tools/deploy/config.py`:
```python
"""Names and places the deploy commands agree on (spec §3.4 and Revision 2)."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGION = "eu-north-1"
DEFAULT_PROFILE = "nettriage"
STAGES = ("dev",)
CI_WORKFLOW = "ci.yml"
CODEQL_WORKFLOW = "codeql.yml"
BACKEND_ARTIFACT = "backend-zip"
WEB_ARTIFACT = "web-dist"
BOOTSTRAP_DIR = REPO / "infra" / "bootstrap"
BOOTSTRAP_STATE_KEY = "bootstrap/terraform.tfstate"
PLUGIN_CACHE = Path.home() / ".terraform.d" / "plugin-cache"


def state_bucket(account_id: str) -> str:
    return f"nettriage-tfstate-{account_id}"


def state_key(stage: str) -> str:
    return f"envs/{stage}/terraform.tfstate"


def otlp_auth_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/grafana-otlp-auth"


def stage_dir(stage: str) -> Path:
    return REPO / "infra" / "envs" / stage
```

`tools/deploy/session.py`:
```python
"""Turn the owner's short-lived `aws login` session into environment credentials for one run."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from tools.deploy.config import REGION
from tools.deploy.runner import CommandError, Runner


def aws_env(run: Runner, profile: str) -> dict[str, str]:
    """This process's environment plus the profile's temporary credentials, in eu-north-1.

    Inherited AWS_* variables are dropped, so only the owner's session reaches AWS. Profiles
    holding long-lived access keys are refused (spec §6.8: no long-lived keys).
    """
    try:
        raw = run(
            ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"]
        ).stdout
    except CommandError as exc:
        raise CommandError(
            f"No usable AWS session for profile '{profile}'. Sign in with: aws login --profile {profile}"
        ) from exc
    creds = json.loads(raw)
    if not creds.get("SessionToken"):
        raise CommandError(
            f"Profile '{profile}' uses long-lived access keys. Use a short-lived session instead: "
            f"aws login --profile {profile}"
        )
    env = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    env.update(
        {
            "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
            "AWS_SESSION_TOKEN": creds["SessionToken"],
            "AWS_REGION": REGION,
            "AWS_DEFAULT_REGION": REGION,
        }
    )
    return env


def account_id(run: Runner, env: Mapping[str, str]) -> str:
    return run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], env=env
    ).stdout.strip()
```

`tools/deploy/secrets.py`:
```python
"""The Grafana OTLP token lives in SSM Parameter Store; it is never printed or written to disk."""

from __future__ import annotations

import base64
from collections.abc import Mapping

from tools.deploy.config import otlp_auth_parameter
from tools.deploy.runner import CommandError, Runner


def read_otlp_auth(run: Runner, env: Mapping[str, str], stage: str) -> str:
    name = otlp_auth_parameter(stage)
    try:
        value = run(
            ["aws", "ssm", "get-parameter", "--name", name, "--with-decryption",
             "--query", "Parameter.Value", "--output", "text"],
            env=env,
        ).stdout.strip()
    except CommandError as exc:
        raise CommandError(
            f"Can't read {name} from SSM. Store it with: just store-grafana-token {stage}"
        ) from exc
    if not value:
        raise CommandError(f"{name} is empty. Store it with: just store-grafana-token {stage}")
    return value


def store_otlp_auth(run: Runner, env: Mapping[str, str], stage: str, value: str) -> None:
    if not value.strip():
        raise CommandError("The token is empty; nothing was stored.")
    run(
        ["aws", "ssm", "put-parameter", "--name", otlp_auth_parameter(stage),
         "--type", "SecureString", "--overwrite", "--value", value.strip()],
        env=env,
    )


def otlp_auth_value(instance_id: str, token: str) -> str:
    """Grafana Cloud's OTLP basic-auth value: base64("<instanceID>:<token>")."""
    instance_id, token = instance_id.strip(), token.strip()
    if not instance_id.isdigit() or not token:
        raise CommandError("Enter the numeric Grafana instance ID and a non-empty token.")
    return base64.b64encode(f"{instance_id}:{token}".encode()).decode()
```

`tools/deploy/terraform.py`:
```python
"""Terraform invocations shared by bootstrap, plan and deploy."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.deploy.config import REGION
from tools.deploy.runner import Runner


def backend_args(bucket: str, key: str) -> list[str]:
    return [
        f"-backend-config=bucket={bucket}",
        f"-backend-config=key={key}",
        f"-backend-config=region={REGION}",
        "-backend-config=use_lockfile=true",
    ]


def init(run: Runner, env: Mapping[str, str], cwd: Path, bucket: str, key: str) -> None:
    run(["terraform", "init", "-input=false", "-reconfigure", *backend_args(bucket, key)], env=env, cwd=cwd)


def planned_changes(plan_json: str) -> list[str]:
    """`<actions> <address>` lines for every real change: never attribute values."""
    lines = []
    for change in json.loads(plan_json).get("resource_changes", []):
        actions = change["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        lines.append(f"{'/'.join(actions)} {change['address']}")
    return lines


def plan(run: Runner, env: Mapping[str, str], cwd: Path, plan_file: Path) -> list[str]:
    run(["terraform", "plan", "-input=false", "-lock=false", f"-out={plan_file}"], env=env, cwd=cwd)
    return planned_changes(run(["terraform", "show", "-json", str(plan_file)], env=env, cwd=cwd).stdout)


def apply(run: Runner, env: Mapping[str, str], cwd: Path, extra_args: Sequence[str] = ()) -> None:
    """Show the plan in the terminal and let the owner confirm it with "yes"."""
    run(["terraform", "apply", "-input=true", *extra_args], env=env, cwd=cwd, interactive=True)


def outputs(run: Runner, env: Mapping[str, str], cwd: Path) -> dict[str, str]:
    data = json.loads(run(["terraform", "output", "-json"], env=env, cwd=cwd).stdout)
    return {name: str(item["value"]) for name, item in data.items()}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just tools-test`
Expected: `50 passed` (30 + 20 new: runner 4, session 4, secrets 8, terraform 4), no warnings.

- [ ] **Step 5: Commit**

```bash
git add tools/deploy tools/tests/deploy_fakes.py tools/tests/test_deploy_runner.py tools/tests/test_deploy_session.py tools/tests/test_deploy_secrets.py tools/tests/test_deploy_terraform.py
git commit -m "feat(tools): deploy tool foundation with session, secrets and Terraform helpers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Deploy guards, CI artifacts and web publishing

**Files:**
- Create: `tools/deploy/gitguards.py`, `tools/deploy/github.py`, `tools/deploy/publish.py`
- Test: `tools/tests/test_deploy_gitguards.py`, `tools/tests/test_deploy_github.py`, `tools/tests/test_deploy_publish.py`

**Interfaces:**
- Consumes: `Runner` and `CommandError` (Task 4); `config.BACKEND_ARTIFACT`/`WEB_ARTIFACT`; `FakeRun`, `runs` (Task 4 fakes).
- Produces:
  - `tools.deploy.gitguards`: `head_sha(run: Runner) -> str` and `require_clean_main(run: Runner) -> str`.
  - `tools.deploy.github`:
    - `WorkflowRun(run_id: int, status: str, conclusion: str, event: str)` (a frozen dataclass)
    - `latest_run(run, workflow: str, sha: str, event: str | None = None) -> WorkflowRun | None`
    - `require_success(run, workflow: str, sha: str, event: str) -> WorkflowRun`
    - `download(run, run_id: int, name: str, dest: Path) -> Path`
    - `comment_on_pr(run, body_file: Path) -> None`
  - `tools.deploy.publish`: `publish_web(run, env, web_dir: Path, bucket: str, distribution_id: str) -> None`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_deploy_gitguards.py`:
```python
import pytest

from tools.deploy import gitguards
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun

SHA = "b" * 40


def repo(branch: str = "main", status: str = "", local: str = SHA, remote: str = SHA) -> FakeRun:
    return (
        FakeRun()
        .on("git", "rev-parse", "--abbrev-ref", returns=f"{branch}\n")
        .on("git", "status", "--porcelain", returns=status)
        .on("git", "fetch")
        .on("git", "rev-parse", "HEAD", returns=f"{local}\n")
        .on("git", "rev-parse", "origin/main", returns=f"{remote}\n")
    )


def test_clean_main_matching_github_is_deployable() -> None:
    run = repo()
    assert gitguards.require_clean_main(run) == SHA
    assert run.called("git", "fetch")[0].args == ["git", "fetch", "--quiet", "origin", "main"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"branch": "plan-1/walking-skeleton"}, "you're on 'plan-1/walking-skeleton'"),
        ({"status": " M README.md\n"}, "uncommitted changes"),
        ({"remote": "c" * 40}, "differs from GitHub"),
    ],
)
def test_anything_else_is_refused(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(CommandError, match=message):
        gitguards.require_clean_main(repo(**kwargs))
```

`tools/tests/test_deploy_github.py`:
```python
from pathlib import Path

import pytest

from tools.deploy import github
from tools.deploy.runner import CommandError
from tools.tests.deploy_fakes import FakeRun, runs

SHA = "a" * 40


def test_latest_run_filters_by_event_newest_first() -> None:
    run = FakeRun().on(
        "gh", "run", "list",
        returns=runs((3, "completed", "success", "pull_request"), (2, "completed", "success", "push")),
    )
    found = github.latest_run(run, "ci.yml", SHA, event="push")
    assert found is not None and found.run_id == 2
    args = run.calls[0].args
    assert args[:5] == ["gh", "run", "list", "--workflow", "ci.yml"]
    assert args[args.index("--commit") + 1] == SHA


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        (runs(), "No ci.yml run"),
        (runs((1, "in_progress", "", "push")), "still in_progress"),
        (runs((1, "completed", "failure", "push")), "concluded 'failure'"),
    ],
)
def test_only_green_commits_pass(listing: str, message: str) -> None:
    run = FakeRun().on("gh", "run", "list", returns=listing)
    with pytest.raises(CommandError, match=message):
        github.require_success(run, "ci.yml", SHA, event="push")


def test_green_run_is_returned() -> None:
    run = FakeRun().on("gh", "run", "list", returns=runs((7, "completed", "success", "push")))
    assert github.require_success(run, "ci.yml", SHA, event="push").run_id == 7


def test_expired_artifacts_are_explained(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "run", "download", returns=CommandError("no artifact matches"))
    with pytest.raises(CommandError, match="expire after 7 days"):
        github.download(run, 7, "backend-zip", tmp_path)


def test_download_names_the_run_artifact_and_directory(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "run", "download")
    assert github.download(run, 7, "web-dist", tmp_path / "web") == tmp_path / "web"
    assert run.calls[0].args == [
        "gh", "run", "download", "7", "--name", "web-dist", "--dir", str(tmp_path / "web"),
    ]


def test_a_branch_without_a_pr_is_explained(tmp_path: Path) -> None:
    run = FakeRun().on("gh", "pr", "comment", returns=CommandError("no pull requests found"))
    with pytest.raises(CommandError, match="--no-comment"):
        github.comment_on_pr(run, tmp_path / "plan.md")
```

`tools/tests/test_deploy_publish.py`:
```python
from pathlib import Path

from tools.deploy import publish
from tools.tests.deploy_fakes import FakeRun


def test_assets_are_cached_forever_index_never_and_demo_is_kept(tmp_path: Path) -> None:
    run = FakeRun().on("aws", "s3", "sync").on("aws", "cloudfront", "create-invalidation")

    publish.publish_web(run, {}, tmp_path, "nettriage-dev-web-1", "E123")

    assets, rest, invalidation = (call.args for call in run.calls)
    assert assets[:5] == ["aws", "s3", "sync", str(tmp_path / "assets"), "s3://nettriage-dev-web-1/assets"]
    assert "public,max-age=31536000,immutable" in assets
    assert rest[3:5] == [str(tmp_path), "s3://nettriage-dev-web-1"]
    assert "--delete" in rest and "no-cache" in rest
    assert rest.count("--exclude") == 2 and "assets/*" in rest and "demo/*" in rest
    assert invalidation[-3:] == ["--paths", "/index.html", "/"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just tools-test`
Expected: `ModuleNotFoundError` for `tools.deploy.gitguards`, `tools.deploy.github` and `tools.deploy.publish`. The 50 earlier tests still pass.

- [ ] **Step 3: Implement**

`tools/deploy/gitguards.py`:
```python
"""Guards that let only a clean, pushed `main` commit deploy."""

from __future__ import annotations

from tools.deploy.runner import CommandError, Runner


def head_sha(run: Runner) -> str:
    return run(["git", "rev-parse", "HEAD"]).stdout.strip()


def require_clean_main(run: Runner) -> str:
    """The commit to deploy: a clean `main` checkout equal to GitHub's `main`."""
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "main":
        raise CommandError(f"Deploys run from main; you're on '{branch}'. Run: git switch main && git pull --ff-only")
    if run(["git", "status", "--porcelain"]).stdout.strip():
        raise CommandError("The working tree has uncommitted changes; commit or stash them first.")
    run(["git", "fetch", "--quiet", "origin", "main"])
    local = head_sha(run)
    remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    if local != remote:
        raise CommandError(f"Local main ({local[:7]}) differs from GitHub's main ({remote[:7]}). Run: git pull --ff-only")
    return local
```

`tools/deploy/github.py`:
```python
"""A commit's CI runs, their artifacts and PR comments, through the GitHub CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tools.deploy.runner import CommandError, Runner


@dataclass(frozen=True)
class WorkflowRun:
    run_id: int
    status: str
    conclusion: str
    event: str


def latest_run(run: Runner, workflow: str, sha: str, event: str | None = None) -> WorkflowRun | None:
    listing = run(
        ["gh", "run", "list", "--workflow", workflow, "--commit", sha,
         "--json", "databaseId,status,conclusion,event", "--limit", "20"]
    ).stdout
    found = [
        WorkflowRun(item["databaseId"], item["status"], item["conclusion"] or "", item["event"])
        for item in json.loads(listing)
    ]
    if event is not None:
        found = [item for item in found if item.event == event]
    return found[0] if found else None  # gh lists the newest run first


def require_success(run: Runner, workflow: str, sha: str, event: str) -> WorkflowRun:
    found = latest_run(run, workflow, sha, event)
    if found is None:
        raise CommandError(f"No {workflow} run found for {sha[:7]} ({event}). Push it and wait for CI.")
    if found.status != "completed":
        raise CommandError(f"{workflow} for {sha[:7]} is still {found.status}; wait for it to finish.")
    if found.conclusion != "success":
        raise CommandError(f"{workflow} for {sha[:7]} concluded '{found.conclusion}'; only green commits deploy.")
    return found


def download(run: Runner, run_id: int, name: str, dest: Path) -> Path:
    try:
        run(["gh", "run", "download", str(run_id), "--name", name, "--dir", str(dest)])
    except CommandError as exc:
        raise CommandError(
            f"Couldn't download '{name}' from run {run_id}. CI artifacts expire after 7 days: "
            "re-run the workflow on GitHub, then try again."
        ) from exc
    return dest


def comment_on_pr(run: Runner, body_file: Path) -> None:
    try:
        run(["gh", "pr", "comment", "--body-file", str(body_file)])
    except CommandError as exc:
        raise CommandError("This branch has no open PR to comment on; open one, or plan with --no-comment.") from exc
```

`tools/deploy/publish.py`:
```python
"""Publish the web build: hashed assets cached for a year, everything else revalidated."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tools.deploy.runner import Runner


def publish_web(run: Runner, env: Mapping[str, str], web_dir: Path, bucket: str, distribution_id: str) -> None:
    run(
        ["aws", "s3", "sync", str(web_dir / "assets"), f"s3://{bucket}/assets",
         "--cache-control", "public,max-age=31536000,immutable"],
        env=env,
    )
    # demo/ holds the public demo snapshot (spec §3.2); --delete must never remove it.
    run(
        ["aws", "s3", "sync", str(web_dir), f"s3://{bucket}",
         "--exclude", "assets/*", "--exclude", "demo/*", "--cache-control", "no-cache", "--delete"],
        env=env,
    )
    run(
        ["aws", "cloudfront", "create-invalidation", "--distribution-id", distribution_id,
         "--paths", "/index.html", "/"],
        env=env,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just tools-test`
Expected: `63 passed` (50 + 13 new: gitguards 4, github 8, publish 1), no warnings.

- [ ] **Step 5: Commit**

```bash
git add tools/deploy/gitguards.py tools/deploy/github.py tools/deploy/publish.py tools/tests/test_deploy_gitguards.py tools/tests/test_deploy_github.py tools/tests/test_deploy_publish.py
git commit -m "feat(tools): deploy guards, CI artifact downloads and web publishing

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Preflight checks

**Files:**
- Create: `tools/deploy/preflight.py`
- Test: `tools/tests/test_deploy_preflight.py`

**Interfaces:**
- Consumes: `Runner` and `CommandError`; `config.REGION`, `config.otlp_auth_parameter` and `config.state_bucket` (Task 4).
- Produces: `tools.deploy.preflight`, containing:
  - `Check(name: str, ok: bool, detail: str = "")`, a frozen dataclass
  - `read_tfvars(path: Path) -> dict[str, str]`
  - `account_checks(run, env, account_id: str) -> list[Check]`, which checks `Lambda in eu-north-1`, `IAM`, `CloudFront`, `Budgets` and `SSM in eu-north-1`
  - `layer_check(run, env, name: str, arn: str) -> Check`
  - `parameter_check(run, env, stage: str) -> Check`
  - `endpoint_check(tfvars: dict[str, str]) -> Check`
  - `stage_checks(run, env, stage: str, account_id: str, tfvars: dict[str, str]) -> list[Check]`
  - `report(checks: list[Check]) -> bool`

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_deploy_preflight.py`:
```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just tools-test`
Expected: `ModuleNotFoundError: No module named 'tools.deploy.preflight'`. The 63 earlier tests still pass.

- [ ] **Step 3: Implement**

`tools/deploy/preflight.py`:
```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just tools-test`
Expected: `71 passed` (63 + 8 new), no warnings.

- [ ] **Step 5: Commit**

```bash
git add tools/deploy/preflight.py tools/tests/test_deploy_preflight.py
git commit -m "feat(tools): read-only preflight checks for the account and the dev stage

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Deploy commands and `just` recipes

**Files:**
- Create: `tools/deploy/__main__.py`
- Test: `tools/tests/test_deploy_cli.py`
- Modify: `justfile` (append five recipes)

**Interfaces:**
- Consumes: everything from Tasks 4–6, plus `tools.smoke.main(argv: list[str] | None = None) -> int` from Plan 1 Task 7.
- Produces: `tools.deploy.__main__`, containing:
  - `run_preflight(run, env, stage: str | None) -> bool`
  - `stage_env(run, env, stage, sha, lambda_zip: Path) -> dict[str, str]`
  - `bootstrap(run, env, budget_email: str, anomaly_monitor_arn: str) -> None`
  - `plan_comment(stage, sha, changes) -> str`
  - `plan(run, env, stage, post_comment: bool) -> list[str]`
  - `deploy(run, env, stage, smoke_main=smoke.main) -> None`
  - `store_grafana_token(run, env, stage, ask_id=input, ask_secret=getpass.getpass) -> None`
  - `main(argv: list[str] | None = None, run: Runner = runner.run) -> int`

  The CLI is `python -m tools.deploy [--profile P] {preflight,bootstrap,store-grafana-token,plan,deploy} …`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_deploy_cli.py`:
```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just tools-test`
Expected: `ModuleNotFoundError: No module named 'tools.deploy.__main__'` for `test_deploy_cli.py`. The 71 earlier tests still pass.

- [ ] **Step 3: Implement**

`tools/deploy/__main__.py`:
```python
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
        except CommandError:
            if not local_state.exists():
                raise
            kept = config.BOOTSTRAP_DIR / "terraform.tfstate.recovered"
            shutil.copy2(local_state, kept)
            raise CommandError(
                f"The first bootstrap apply failed; its partial state is saved in {kept} (git-ignored). "
                "Keep that file and ask for help before retrying."
            ) from None
        terraform.init(run, env, config.BOOTSTRAP_DIR, bucket, config.BOOTSTRAP_STATE_KEY)
        run(["terraform", "state", "push", str(local_state)], env=env, cwd=config.BOOTSTRAP_DIR)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just tools-test`
Expected: `86 passed` (71 + 15 new), no warnings.

- [ ] **Step 5: Add the recipes**

Append to `justfile`:
```just

# AWS (read-only): check the account still allows what a deploy needs
preflight stage="dev":
    uv run --project backend python -m tools.deploy preflight --stage {{stage}}

# AWS, once: create the state bucket and budget alerts, then move their state into S3
bootstrap budget_email anomaly_monitor_arn="":
    uv run --project backend python -m tools.deploy bootstrap --budget-email "{{budget_email}}" --anomaly-monitor-arn "{{anomaly_monitor_arn}}"

# AWS, once per stage: store the Grafana OTLP token in SSM Parameter Store
store-grafana-token stage="dev":
    uv run --project backend python -m tools.deploy store-grafana-token --stage {{stage}}

# Plan the dev stage for the checked-out, pushed commit and post the changes to its PR
plan-dev:
    uv run --project backend python -m tools.deploy plan --stage dev

# Deploy main's CI-built artifacts to dev, then run the smoke tests
deploy-dev:
    uv run --project backend python -m tools.deploy deploy --stage dev
```

Run: `just --list`
Expected: the five new recipes are listed with their comments.

Run: `uv run --project backend python -m tools.deploy --help`
Expected: the usage text from the module docstring. The command makes no AWS calls; `--help` exits before any session is read.

- [ ] **Step 6: Commit**

```bash
git add tools/deploy/__main__.py tools/tests/test_deploy_cli.py justfile
git commit -m "feat(tools): preflight, bootstrap, plan and deploy commands for the owner

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Decision records, README and CLAUDE.md

**Files:**
- Create: `docs/adr/0013-owner-run-deploys-short-lived-credentials.md`
- Modify: `docs/adr/0011-terraform-one-account-two-stages.md`, `docs/adr/0002-neon-postgres.md`
- Modify: `README.md`, `CLAUDE.md`, `tools/smoke.py` (docstring only)
- Modify: `docs/superpowers/plans/2026-09-26-nettriage-plan-1-walking-skeleton.md` (one note)

**Interfaces:**
- Consumes: the recipe names from Tasks 3 and 7 (`cloud-check`, `preflight`, `bootstrap`, `store-grafana-token`, `plan-dev`, `deploy-dev`), and the runbook path `docs/runbooks/setup-and-deploy.md`, which Task 9 creates.
- Produces: ADR 0013, which the README links to.

- [ ] **Step 1: Write ADR 0013**

`docs/adr/0013-owner-run-deploys-short-lived-credentials.md`:
```markdown
# 0013: Owner-run deploys with short-lived credentials; CI holds no cloud access

- Status: Accepted
- Date: 2026-09-27

## Context
The AWS account was created with AWS's newer sign-up experience, which places it in an
AWS-managed organization (spec Revision 2). Its policies deny creating IAM identity providers
on both the Free and the Paid plan, so GitHub Actions can't sign in to the account with OIDC,
which ADR 0011 relied on. Lifting that restriction is irreversible ("Activate advanced
features") or forfeits the account's $120 of Free plan credits. The one option left inside the
rules, an IAM user whose access key is stored in GitHub, would put a long-lived key in CI.

## Decision
Split CI from CD.
- GitHub Actions runs every check and builds the deployable artifacts once (the Lambda zip and
  the web build), with no AWS access of any kind. `tools/check_no_cloud_access.py` fails CI if
  a workflow asks for an OIDC token, uses an AWS action or references AWS credentials, or if
  Terraform defines a GitHub OIDC trust.
- The owner deploys from their machine with a short-lived `aws login` session. `just deploy-dev`
  runs the preflight checks, accepts only a clean `main` checkout equal to GitHub's `main` whose
  `ci` and `codeql` runs succeeded, downloads that commit's CI artifacts, applies Terraform
  (the owner confirms the plan), publishes the site and runs the smoke tests.
- `just plan-dev` plans a pushed PR commit with its CI artifacts and posts the planned
  changes, addresses only, to the PR.
- Deploy secrets live in SSM Parameter Store as SecureStrings and are read at deploy time.

## Consequences
- No stored cloud credentials exist anywhere: not in GitHub, not on disk, not as IAM users. A
  compromised workflow or dependency in CI can't reach AWS at all.
- What's deployed is exactly what CI built and tested, and only after CI and CodeQL passed for
  that commit.
- Deploys are deliberate owner actions; nothing deploys on merge. They depend on the owner's
  machine and sign-in, and CI artifacts expire after 7 days (re-running CI refreshes them).
- Unattended jobs that need AWS (nightly evals, backups) can't run in GitHub Actions. They run
  inside AWS as scheduled Lambdas or as owner-run commands (Plans 5 and 7).
- The bootstrap no longer creates an OIDC provider, CI roles or a permissions boundary.

## Alternatives considered
- **GitHub OIDC (ADR 0011's original design):** blocked by the account's policies.
- **Activate advanced features:** unlocks OIDC and us-east-1, but it's irreversible and needs
  the Paid plan, which forfeits the credits.
- **A new standalone AWS account:** existing AWS customers get no Free plan credits.
- **An IAM user's access key in GitHub secrets:** a long-lived key in CI.
- **A self-hosted GitHub runner on the owner's machine:** any PR's jobs would run next to the
  owner's credentials.
```

- [ ] **Step 2: Update ADRs 0011 and 0002**

Replace `docs/adr/0011-terraform-one-account-two-stages.md` with:
```markdown
# 0011: Terraform, one account, two stages

- Status: Accepted; amended by [ADR 0013](0013-owner-run-deploys-short-lived-credentials.md) on 2026-09-27
- Date: 2026-09-26

## Context
The project needs repeatable, reviewable infrastructure across a `dev` and a `prod` stage,
state locking that is safe for concurrent applies, and deploys that never rely on long-lived
AWS credentials.

## Decision
Terraform 1.11 or later, with native S3 state locking (`use_lockfile = true`, no DynamoDB lock
table). The owner applies a one-time bootstrap stack (the state bucket and budget alerts) with
`just bootstrap`; everything after that changes only through the reviewed, owner-run deploy
command (ADR 0013). `dev` and `prod` share a single AWS account, in eu-north-1 (spec
Revision 2).

## Consequences
- Stages are isolated by naming convention, by separate IAM roles and by separate Terraform
  state keys rather than by account boundary.
- No long-lived AWS access keys exist anywhere; deploys use the owner's short-lived `aws login`
  session, and CI holds no cloud access (ADR 0013).

## Alternatives considered
- **AWS CDK (Python):** familiar language, but it locks the project into AWS-specific tooling
  with less mature multi-cloud portability than Terraform.
- **CloudFormation:** native to AWS and free, but a weaker module reuse and testing story than
  Terraform for a project this size.
- **Multi-account stages:** the strongest isolation between stages. Not needed while only `dev`
  exists; Plan 7 revisits it when it adds `prod`.
```

In `docs/adr/0002-neon-postgres.md`:
- Change line 4 to `- Date: 2026-09-26 (region updated 2026-09-27)`.
- Replace the Decision paragraph (lines 13–15) with:

```markdown
## Decision
Use Neon's free tier, in region aws-eu-central-1 (Frankfurt), the closest Neon region to the
API in eu-north-1 (spec Revision 2, D2). Only standard Postgres features are used, so the
database stays portable to Aurora or RDS if the free tier is ever outgrown.
```

- [ ] **Step 3: Update the README**

In `README.md`:
- Replace the status quote (lines 6–7) with:
  ```markdown
  > Status: Milestone 1 in progress. Plan 1 (walking skeleton) delivers the deployed,
  > observable foundation: CI, owner-run deploys, infrastructure as code and telemetry.
  ```
- After the architecture paragraph that ends "…has an [architecture decision record](docs/adr/).", add this paragraph:
  ```markdown
  Regional resources run in eu-north-1 (Stockholm); CloudFront serves the app worldwide.
  ```
- Replace the "No long-lived keys" highlight bullet with:
  ```markdown
  - **No stored cloud credentials**: CI checks and builds with no AWS access; the owner deploys CI-built, CI-green commits with a short-lived sign-in ([ADR 0013](docs/adr/0013-owner-run-deploys-short-lived-credentials.md)). The Lambda Function URL only accepts CloudFront-signed requests ([ADR 0003](docs/adr/0003-cloudfront-to-function-url-with-oac.md)).
  ```
- In the Develop code block, add this line after `just tf-check …`:
  `just cloud-check # CI holds no cloud access`
- Replace the line `Deployments run from GitHub Actions only: merging to \`main\` deploys the \`dev\` stage and runs smoke tests.` with:
  ````markdown
  ## Deploy

  Only the owner deploys, from their machine, with a short-lived AWS sign-in. The step-by-step
  guide is [docs/runbooks/setup-and-deploy.md](docs/runbooks/setup-and-deploy.md).

  ```bash
  aws login --profile nettriage   # short-lived session
  just preflight                  # read-only checks of the account
  just plan-dev                   # on a PR: plan and post the changes
  just deploy-dev                 # on main: deploy CI's artifacts, then smoke tests
  ```
  ````

- [ ] **Step 4: Update CLAUDE.md, the smoke docstring and Plan 1**

In `CLAUDE.md`, replace the Commands section body (line 9) with:
```markdown
Run `just` to list tasks. Backend commands run inside `backend/` with `uv run …`. On the owner's
Windows machine, run Python tools as modules (`uv run python -m pytest`); host policy blocks
some uv launchers.
Deploys are owner actions (`docs/runbooks/setup-and-deploy.md`). Claude may run `just preflight`
and `just plan-dev` when the owner asks, but never runs `aws login`, `just bootstrap`,
`just store-grafana-token` or `just deploy-*`.
```

Replace the last Conventions bullet (line 23) with:
```markdown
- Infrastructure changes go through Terraform, reviewed in a PR and applied with
  `just deploy-<stage>`, never through console clicks. CI holds no cloud access (ADR 0013).
  Regional resources live in eu-north-1.
```

In `tools/smoke.py`, change `https://xxxx.lambda-url.us-east-1.on.aws/` in the module docstring to `https://xxxx.lambda-url.eu-north-1.on.aws/`.

In `docs/superpowers/plans/2026-09-26-nettriage-plan-1-walking-skeleton.md`, insert this note directly after the `**Plan series:**` paragraph (line 13), followed by a blank line:
```markdown
> **Superseded in part (2026-09-27):** the account's AWS-managed policies rule out GitHub OIDC and us-east-1 (spec Revision 2). Task 8 Steps 6–7, Task 10 Step 6's values, Task 11 Step 6, Task 12 and Task 14 Steps 5–8 are replaced by [Plan 1b](2026-09-27-nettriage-plan-1b-account-adaptation.md) and [docs/runbooks/setup-and-deploy.md](../../runbooks/setup-and-deploy.md).
```

- [ ] **Step 5: Verify and commit**

Run: `ls docs/adr | wc -l`. Expected: `14`.
Run: `just tools-test`. Expected: `86 passed`. The smoke docstring change is behavior-neutral.
Run: `grep -rn "Deployments run from GitHub Actions\|GitHub Actions deploys through OIDC" README.md docs/adr CLAUDE.md`. Expected: no output.

```bash
git add docs/adr README.md CLAUDE.md tools/smoke.py docs/superpowers/plans/2026-09-26-nettriage-plan-1-walking-skeleton.md
git commit -m "docs: ADR 0013 and the owner-run deploy model across the README and ADRs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The owner's setup-and-deploy runbook

**Files:**
- Create: `docs/runbooks/setup-and-deploy.md`

**Interfaces:**
- Consumes: the exact recipe names, arguments and output lines from Tasks 3, 6 and 7:
  - `preflight`'s `PASS`/`FAIL` lines;
  - `STOP:` messages;
  - `### Terraform plan: dev (<sha>)`;
  - `Deployed <sha> to dev: https://…`;
  - the eight smoke-check names from `tools/smoke.py`.
- Produces: the document that spec §14 and the README link to.

- [ ] **Step 1: Write the runbook**

`docs/runbooks/setup-and-deploy.md`:
````markdown
# Setup and deploy (owner runbook)

Only the owner runs these steps, from their own machine. They cost $0: the AWS account is on
the Free plan until 2027-03-08 and can't be charged (spec Revision 2). Commands are for **Git
Bash in Windows Terminal**, from `C:\dev\nettriage`. Password and token prompts need a real
terminal, not an embedded one.

## Part A: one-time setup

### A1. Check the tools
```bash
aws --version; terraform version; gh auth status; just --version; uv --version
```
The AWS CLI must be 2.32 or later (for `aws login`), and `gh` must be signed in.

### A2. Sign in to AWS from the command line
```bash
aws login --profile nettriage
aws configure set region eu-north-1 --profile nettriage
aws sts get-caller-identity --profile nettriage
```
`aws login` opens your browser; sign in as you do on the AWS console. The last command prints
your account ID. The session is short-lived: when a command later says
`STOP: No usable AWS session`, run `aws login --profile nettriage` again. Never create access
keys; the deploy tool refuses them.

### A3. Create the Grafana Cloud stack
1. Create a free stack at grafana.com.
2. Open **Connections → OpenTelemetry (OTLP)** and note three values:
   - the **OTLP endpoint** (it starts with `https://`);
   - the **instance ID** (a number);
   - a new **token** with the `metrics:write`, `logs:write` and `traces:write` scopes.
3. Put the endpoint in `infra/envs/dev/terraform.tfvars` (`grafana_otlp_endpoint = "…"`) through
   a PR, or send it to Claude to add. It isn't a secret.
4. Store the token in AWS (it never goes into Git or GitHub):
   ```bash
   just store-grafana-token dev
   ```
   Enter the instance ID, then paste the token; the token isn't shown. Expected:
   `Stored /nettriage/dev/grafana-otlp-auth as a SecureString.`

### A4. Bootstrap the account (once)
Optional: find the account's cost anomaly monitor, so anomaly alerts also email you:
```bash
aws ce get-anomaly-monitors --profile nettriage --region us-east-1 --query 'AnomalyMonitors[].[MonitorName,MonitorArn]' --output table
```
Then run one of these (use your email; add the monitor ARN if one was listed):
```bash
just bootstrap you@example.com
just bootstrap you@example.com arn:aws:ce::123456789012:anomalymonitor/…
```
The first lines are the account checks (`PASS  Lambda in eu-north-1`, `PASS  IAM`, …). If any
line says `FAIL`, stop and send it to Claude. Terraform then shows the plan: the state bucket
`nettriage-tfstate-<account>` with its settings, the `nettriage-monthly` budget and, optionally,
the anomaly subscription. Type `yes`. It ends with
`Bootstrap state is now in s3://nettriage-tfstate-<account>/bootstrap/terraform.tfstate`.
Confirm the AWS Budgets email if one arrives.

### A5. Run the preflight
```bash
just preflight
```
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
Grafana endpoint and both Lambda layers. If a layer line fails with "not found", its version
moved on. Ask Claude to update the ARN in `terraform.tfvars` from the layer's release notes.

### A6. Set up the GitHub repository (once)
```bash
gh repo edit --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false --delete-branch-on-merge
gh api -X PUT "repos/{owner}/{repo}/vulnerability-alerts"
gh api -X PUT "repos/{owner}/{repo}/automated-security-fixes"
gh api -X PUT "repos/{owner}/{repo}/private-vulnerability-reporting"
echo '{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}' \
  | gh api -X PATCH "repos/{owner}/{repo}" --input -
```
If the PR's `dependency-review` check still says "Dependency graph is not enabled", turn on
**Dependency graph** at https://github.com/khajdar1/nettriage/settings/security_analysis.

Protect `main`. Approvals are 0 because you can't approve your own PR; required checks,
resolved threads and linear history still apply:
```bash
cat > "$TEMP/ruleset.json" <<'EOF'
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "backend" }, { "context": "package" }, { "context": "frontend" },
          { "context": "edge-functions" }, { "context": "terraform" },
          { "context": "analyze (python)" }, { "context": "analyze (javascript-typescript)" },
          { "context": "analyze (actions)" } ] } }
  ]
}
EOF
gh api -X POST "repos/{owner}/{repo}/rulesets" --input "$TEMP/ruleset.json"
gh api "repos/{owner}/{repo}/rulesets" --jq '.[].name'
```
The last command prints `protect-main`.

Check Copilot code review at github.com/settings/copilot (choose **Lite** effort if the setting
exists). Then connect the GitHub MCP server for Claude's review loop:
1. Create a fine-grained token at github.com/settings/personal-access-tokens/new.
   - Repository access: **only `nettriage`**.
   - Permissions: **Pull requests: Read and write** and **Contents: Read-only**.
   - Expiration: 90 days.
2. In a terminal (not inside Claude), run:
   ```bash
   claude mcp add-json github '{"type":"http","url":"https://api.githubcopilot.com/mcp/x/pull_requests","headers":{"Authorization":"Bearer <token>"}}'
   claude mcp list
   ```
   `github` shows as connected. If `request_copilot_review` is missing from `/mcp` in Claude,
   also add `https://api.githubcopilot.com/mcp/x/copilot` the same way under the name
   `github-copilot`.

GitHub needs no deployment environments, variables or secrets for NetTriage.

## Part B: every change

### B1. Review
1. Claude opens the PR, and CI must go green.
2. While the PR's branch is checked out, plan it:
   ```bash
   git switch <pr-branch> && git pull --ff-only
   just plan-dev
   ```
   The planned changes appear in the terminal and as a PR comment titled
   `### Terraform plan: dev (<sha>)`: resource addresses only, never values. On the first
   deploy it lists the dev stage's resources (all `create`).
3. Claude runs the Copilot review loop. You read the diff and the threads, then **squash-merge**
   on GitHub. Claude never merges.

### B2. Deploy
```bash
git switch main && git pull --ff-only
just deploy-dev
```
Wait until the `ci` and `codeql` runs for the merge commit are green on GitHub first. The
command:
1. runs the preflight;
2. refuses anything but a clean `main` that matches GitHub and whose CI and CodeQL passed;
3. downloads that commit's CI-built artifacts;
4. shows the Terraform plan, and you type `yes`;
5. publishes the site;
6. runs the smoke tests.

Success looks like eight `PASS` lines:
- `api health`
- `api security headers`
- `web root`
- `web security headers`
- `spa route serves index.html`
- `edge rejects api call without session`
- `api 404 stays problem+json`
- `function url rejects direct calls`

The last line is `Deployed <sha> to dev: https://<id>.cloudfront.net`. The first deploy takes
longer, because CloudFront needs several minutes to create the distribution.

### B3. Check telemetry and cost
1. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-api" && resource.deployment.environment.name = "dev" }`.
   Traces for `GET /api/health` from the smoke tests appear within a few minutes.
2. In **Explore → Prometheus**, search the metrics for `nettriage-api`; look for
   `http_server_duration_milliseconds_*` or `http_server_request_duration_seconds_*`.
3. In AWS Settings → **Billing**, the amount due is still $0.

## Part C: when things go wrong

### Roll back
On GitHub, open the merged PR and choose **Revert**, which opens a revert PR. Merge it after CI
passes, then run B2 again.

### Messages and fixes
| Message | What to do |
|---|---|
| `STOP: No usable AWS session …` | `aws login --profile nettriage` |
| `STOP: Profile 'nettriage' uses long-lived access keys …` | Delete the keys from `~/.aws/credentials`, then `aws login --profile nettriage` |
| `FAIL  Lambda in eu-north-1`, `FAIL  CloudFront` or another account check (`AccessDenied`, "explicit deny") | AWS changed the account's policies. Don't retry; send the output to Claude |
| `FAIL  Terraform state bucket` | Run A4 (bootstrap) |
| `FAIL  Grafana token in SSM` | Run `just store-grafana-token dev` |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
| `FAIL  … layer … not found` | Ask Claude to update the layer ARN to its current version |
| `STOP: Deploys run from main …`, `… uncommitted changes …` or `… differs from GitHub's main …` | Follow the command in the message |
| `STOP: ci.yml for <sha> is still in_progress` or `concluded 'failure'` | Wait for CI, or fix it; only green commits deploy |
| `STOP: Couldn't download … CI artifacts expire after 7 days …` | On GitHub, re-run the `ci` workflow for that commit, then deploy again |
| `STOP: Smoke tests failed …` | Read the `FAIL` lines. Send them to Claude, or roll back |
| `Error acquiring the state lock` | Another plan or deploy is running, or one was interrupted. Wait a minute and retry; if it persists, send the lock ID to Claude |
| `STOP: The first bootstrap apply failed; its partial state is saved …` | Keep `infra/bootstrap/terraform.tfstate.recovered`, and send the output to Claude before retrying |

### The Free plan deadline
The free plan ends on **2027-03-08**. Before then, decide whether to upgrade: that costs about
$0–1 a month, and the $120 of credits are forfeited. The alternative is to let the account close:
AWS keeps the data for 90 days. Plan 7 adds a `just destroy-dev` command for tearing the stage
down cleanly.
````

- [ ] **Step 2: Check the runbook against the code**

Run each of these and confirm every name the runbook quotes matches the code exactly:
```bash
just --list
grep -n "Check(\"" tools/smoke.py
grep -n "STOP\|Deployed\|Stored\|Bootstrap state" tools/deploy/__main__.py
grep -rn "_probe\|Check(" tools/deploy/preflight.py
```
Expected:
- the recipe names `preflight`, `bootstrap`, `store-grafana-token`, `plan-dev` and `deploy-dev`;
- the eight smoke-check names;
- the success and `STOP:` messages;
- the preflight check names, exactly as quoted in the runbook.

Fix the runbook if anything differs; the code is the source of truth.

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/setup-and-deploy.md
git commit -m "docs: the owner's setup-and-deploy runbook

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10 (owner, with Claude): First deploy, end to end

**Depends on:** Tasks 1–9 pushed to PR #1 with CI green. Claude runs the steps marked (Claude), and the owner runs the rest, following `docs/runbooks/setup-and-deploy.md`.

- [ ] **Step 1 (Claude):** Push the branch, run `gh pr checks 1 --watch`, and update the PR description to describe Plan 1b. Expected: the eight required checks and `CodeQL` pass. `dependency-review` passes once the owner turns on the dependency graph.
- [ ] **Step 2 (owner):** Runbook A1–A3. Send the Grafana OTLP endpoint to Claude.
- [ ] **Step 3 (Claude):** Put the endpoint in `infra/envs/dev/terraform.tfvars`, commit (`chore(infra): dev Grafana OTLP endpoint`, with the trailer) and push.
- [ ] **Step 4 (owner):** Runbook A4 (bootstrap) and A5 (preflight). Expected: every preflight line says `PASS`. If a layer check fails, Claude updates the ARN and the owner re-runs A5.
- [ ] **Step 5 (owner):** Runbook A6 (GitHub settings, ruleset, Copilot, MCP).
- [ ] **Step 6 (owner):** Runbook B1.2 on `plan-1/walking-skeleton` (`just plan-dev`). Expected: a PR comment listing only `create` actions for `nettriage-dev-*` resources, the CloudFront distribution, its functions and policies, and the two Lambda permissions.
- [ ] **Step 7 (Claude):** Mark the PR ready and run the Copilot review loop (spec §11.6). Fix valid comments test-first; reply to the others; resolve every thread.
- [ ] **Step 8 (owner):** Squash-merge PR #1 on GitHub.
- [ ] **Step 9 (owner):** Runbook B2 (`just deploy-dev`). Expected: eight `PASS` lines and `Deployed <sha> to dev: https://<id>.cloudfront.net`.
- [ ] **Step 10 (owner):** Runbook B3. Expected: traces and metrics in Grafana, and $0 due in AWS Billing.

## Plan 1b is done when

- [ ] `just lint test tools-test edge-test web-check tf-check pin-check cloud-check` passes locally, and tools-test reports `86 passed`.
- [ ] PR #1 is merged through the review loop with every thread resolved, and its CI (including CodeQL) is green.
- [ ] `just deploy-dev` ends with eight `PASS` smoke checks.
- [ ] Traces and metrics from `dev` are visible in Grafana Cloud.
- [ ] AWS Settings → Billing shows $0 due.

## Spec coverage of this plan

| Spec (revision 2) | Covered here |
|---|---|
| D2 regions; §3.4 | Tasks 1, 2 (eu-north-1 provider, state bucket, layer validation); Task 8 (ADR 0002: Neon in Frankfurt) |
| D3 CI holds no cloud access; §11.5 hardening | Task 3 (jobs removed, `check_no_cloud_access`, CI step) |
| D4 owner deploys with guards; §11.5 plan and deploy | Tasks 4–7 (`tools/deploy`, recipes) |
| D5 smaller bootstrap; §11.7 | Task 1; Task 7 (`bootstrap` with state push) |
| D6 secrets in SSM; §6.8 | Task 4 (`secrets`), Task 7 (`store-grafana-token`) |
| D9 preflight before every deploy | Task 6; Task 7 (`deploy` runs it first) |
| §3.3 decision 13; §11.9 ADRs; §14 | Task 8 (ADR 0013, README, CLAUDE.md), Task 9 (runbook) |
| D7, D8 | No code in Plan 1b: evals and backups are Plans 5 and 7, and WAF is not used |
| §12 Delivered | Task 10 |
