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
# Neon in AWS Europe (Frankfurt), spec Revision 2 D2. The owner creates the project by hand
# (spec §13.2): the Neon Terraform provider isn't code-signed, and the owner's Windows host
# blocks unsigned executables.
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy. Plans 4, 5 and 7 add theirs.
APP_DB_ROLES = ("app_api",)
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"


def state_bucket(account_id: str) -> str:
    return f"nettriage-tfstate-{account_id}"


def state_key(stage: str) -> str:
    return f"envs/{stage}/terraform.tfstate"


def otlp_auth_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/grafana-otlp-auth"


def stage_dir(stage: str) -> Path:
    return REPO / "infra" / "envs" / stage


def db_owner_url_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/db/owner-url"


def db_role_url_parameter(stage: str, role: str) -> str:
    """For `app_api`: /nettriage/<stage>/db/app-api-url, which the function reads at cold start."""
    return f"/nettriage/{stage}/db/{role.replace('_', '-')}-url"
