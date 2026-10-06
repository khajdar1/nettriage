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
# The ops function's Postgres client and server, built in CI (Plan 7a §4).
PG_CLIENT_ARTIFACT = "pg-client-zip"
WEB_ARTIFACT = "web-dist"
BOOTSTRAP_DIR = REPO / "infra" / "bootstrap"
BOOTSTRAP_STATE_KEY = "bootstrap/terraform.tfstate"
PLUGIN_CACHE = Path.home() / ".terraform.d" / "plugin-cache"
# Neon in AWS Europe (Frankfurt), spec Revision 2 D2. The owner creates the project by hand
# (spec §13.2): the Neon Terraform provider isn't code-signed, and the owner's Windows host
# blocks unsigned executables.
NEON_HOST_SUFFIX = ".eu-central-1.aws.neon.tech"
# Database roles that get a login from the deploy: one per function, and the ops function's two
# (Plan 7a §5). The backup role connects directly: pg_dump needs a session, and the pooler
# works by transaction.
APP_DB_ROLES = ("app_api", "app_analyze", "app_triage", "app_ops", "app_backup")
DIRECT_DB_ROLES = ("app_backup",)
# Where the account may invoke Bedrock (spec Revision 2, R1).
BEDROCK_REGIONS = ("eu-north-1", "us-east-1", "us-west-2")
MIGRATIONS_CONFIG = REPO / "backend" / "alembic.ini"


def state_bucket(account_id: str) -> str:
    return f"nettriage-tfstate-{account_id}"


def state_key(stage: str) -> str:
    return f"envs/{stage}/terraform.tfstate"


def otlp_auth_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/grafana-otlp-auth"


def stage_dir(stage: str) -> Path:
    return REPO / "infra" / "envs" / stage


def uploads_enabled_parameter(stage: str) -> str:
    """The uploads kill switch (spec §9.7), which Terraform creates as "true"."""
    return f"/nettriage/{stage}/kill/uploads-enabled"


def ai_enabled_parameter(stage: str) -> str:
    """The AI kill switch (spec §9.7), which Terraform creates as "true"."""
    return f"/nettriage/{stage}/kill/ai-enabled"


def ops_function(stage: str) -> str:
    """The function that runs the scheduled jobs (infra/modules/ops)."""
    return f"nettriage-{stage}-ops"


def db_owner_url_parameter(stage: str) -> str:
    return f"/nettriage/{stage}/db/owner-url"


def db_role_url_parameter(stage: str, role: str) -> str:
    """For `app_api`: /nettriage/<stage>/db/app-api-url, which the function reads at cold start."""
    return f"/nettriage/{stage}/db/{role.replace('_', '-')}-url"
