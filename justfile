set shell := ["bash", "-cu"]
set windows-shell := ["bash", "-cu"]

# List available recipes
default:
    @just --list

# Backend: lint, format check and type check
lint:
    cd backend && uv run python -m ruff check . && uv run python -m ruff format --check . && uv run python -m mypy

# Backend: auto-format and auto-fix
fmt:
    cd backend && uv run python -m ruff format . && uv run python -m ruff check --fix .

# Backend: tests
test:
    cd backend && uv run python -m pytest --cov=nettriage.domain --cov-report=term-missing --cov-fail-under=85

# Tools: tests for the build, smoke and pinned-actions scripts
tools-test:
    uv run --project backend python -m pytest tools/tests

# Build the Lambda zip (arm64, python3.14) into dist/backend.zip
build-lambda:
    uv run --project backend python tools/build_lambda.py --out dist/backend.zip

# Frontend: install, lint, test, build and CSP-check
web-check:
    cd frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm test && pnpm test:scripts && pnpm build && pnpm check:csp

# CloudFront Functions tests
edge-test:
    node --test infra/modules/edge/functions/*.test.mjs

# Terraform: format, validate and test every stack. The checks use their own data directory
# (.terraform-check), so they never touch the S3 backend that `just bootstrap` initializes in
# infra/bootstrap/.terraform, and they need no AWS session.
tf-check:
    terraform fmt -check -recursive infra
    for d in infra/bootstrap infra/modules/app infra/modules/edge; do (cd "$d" && export TF_DATA_DIR=.terraform-check && terraform init -backend=false -input=false >/dev/null && terraform validate && terraform test) || exit 1; done

# CI hygiene: every workflow action pinned to a SHA
pin-check:
    uv run --project backend python tools/check_pinned_actions.py .github/workflows

# CI hygiene: no workflow or Terraform gives CI access to AWS (ADR 0013)
cloud-check:
    uv run --project backend python tools/check_no_cloud_access.py .

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
