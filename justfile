set shell := ["bash", "-cu"]
set windows-shell := ["bash", "-cu"]

# List available recipes
default:
    @just --list

# Backend: lint, format check and type check
lint:
    cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy

# Backend: auto-format and auto-fix
fmt:
    cd backend && uv run ruff format . && uv run ruff check --fix .

# Backend: tests
test:
    cd backend && uv run pytest
