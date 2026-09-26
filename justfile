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
    cd backend && uv run python -m pytest

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
