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
