"""Settings and secrets from SSM Parameter Store, read once per cold start (spec §6.8): the
functions get parameter names through their environment, never values."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from botocore.config import Config

if TYPE_CHECKING:
    from types_boto3_ssm.client import SSMClient

# Fail fast inside the API's 29-second timeout, with a few quick retries (spec §3.5).
AWS_CONFIG = Config(
    connect_timeout=2, read_timeout=5, retries={"mode": "standard", "max_attempts": 3}
)


class MissingParameterError(RuntimeError):
    """An SSM parameter a function needs doesn't exist. The message names it, never a value."""


def read_parameters(ssm: SSMClient, names: Sequence[str]) -> dict[str, str]:
    response = ssm.get_parameters(Names=list(names), WithDecryption=True)
    missing = response.get("InvalidParameters", [])
    if missing:
        raise MissingParameterError(f"Missing SSM parameters: {', '.join(sorted(missing))}")
    return {parameter["Name"]: parameter["Value"] for parameter in response["Parameters"]}
