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
