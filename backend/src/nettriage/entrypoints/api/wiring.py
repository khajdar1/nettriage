"""Building the API's services in Lambda, once per cold start (spec §6.8): settings and secrets
come from SSM Parameter Store, never from environment variables or the package."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

import boto3
import httpx
from botocore.config import Config

from nettriage.adapters.idempotency import IdempotencyStore
from nettriage.adapters.kill_switch import KillSwitch, ssm_parameter
from nettriage.adapters.login_states import LoginStateStore
from nettriage.adapters.oidc import OidcClient, OidcSettings
from nettriage.adapters.postgres import create_database_engine
from nettriage.adapters.rate_limiter import RateLimiter
from nettriage.adapters.sessions import SessionStore
from nettriage.adapters.upload_storage import UploadStorage, uploads_client
from nettriage.application.clock import system_clock
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings
from nettriage.platform.metrics import AppMetrics

if TYPE_CHECKING:
    from types_boto3_ssm.client import SSMClient

# Fail fast inside the API's 29-second timeout, with a few quick retries (spec §3.5).
AWS_CONFIG = Config(
    connect_timeout=2, read_timeout=5, retries={"mode": "standard", "max_attempts": 3}
)
COGNITO_TIMEOUT = httpx.Timeout(5.0)


class MissingParameterError(RuntimeError):
    """An SSM parameter the API needs doesn't exist. The message names it; it has no value."""


def read_parameters(ssm: SSMClient, names: Sequence[str]) -> dict[str, str]:
    response = ssm.get_parameters(Names=list(names), WithDecryption=True)
    missing = response.get("InvalidParameters", [])
    if missing:
        raise MissingParameterError(f"Missing SSM parameters: {', '.join(sorted(missing))}")
    return {parameter["Name"]: parameter["Value"] for parameter in response["Parameters"]}


def build_services(settings: Settings, session: boto3.session.Session | None = None) -> Services:
    session = session or boto3.session.Session()
    ssm = session.client("ssm", config=AWS_CONFIG)
    values = read_parameters(
        ssm,
        [settings.oidc_parameter, settings.oidc_secret_parameter, settings.database_url_parameter],
    )
    oidc = json.loads(values[settings.oidc_parameter])
    dynamodb = session.client("dynamodb", config=AWS_CONFIG)
    table = settings.runtime_table
    return Services(
        database=create_database_engine(values[settings.database_url_parameter], pool_size=1),
        sessions=SessionStore(dynamodb, table),
        login_states=LoginStateStore(dynamodb, table),
        rate_limiter=RateLimiter(dynamodb, table, system_clock),
        idempotency=IdempotencyStore(dynamodb, table),
        upload_storage=UploadStorage(uploads_client(session), settings.uploads_bucket),
        uploads_switch=KillSwitch(
            ssm_parameter(ssm, settings.uploads_enabled_parameter), system_clock
        ),
        oidc=OidcClient(
            OidcSettings(
                issuer=oidc["issuer"],
                client_id=oidc["client_id"],
                client_secret=values[settings.oidc_secret_parameter],
                domain=oidc["domain"],
                app_origin=oidc["app_origin"],
            ),
            httpx.Client(timeout=COGNITO_TIMEOUT),
            system_clock,
        ),
        clock=system_clock,
        metrics=AppMetrics(),
    )
