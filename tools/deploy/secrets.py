"""Deploy secrets live in SSM Parameter Store as SecureStrings (spec §6.8): the Grafana OTLP
token and the database connection strings. They are never printed or written to disk."""

from __future__ import annotations

import base64
from collections.abc import Mapping

from tools.deploy.config import otlp_auth_parameter
from tools.deploy.runner import CommandError, Runner


def read_parameter(run: Runner, env: Mapping[str, str], name: str, store_with: str) -> str:
    """A SecureString's decrypted value. `store_with` is the command that stores it."""
    try:
        value = run(
            ["aws", "ssm", "get-parameter", "--name", name, "--with-decryption",
             "--query", "Parameter.Value", "--output", "text"],
            env=env,
        ).stdout.strip()
    except CommandError as exc:
        raise CommandError(f"Can't read {name} from SSM. Store it with: {store_with}") from exc
    if not value:
        raise CommandError(f"{name} is empty. Store it with: {store_with}")
    return value


def store_parameter(run: Runner, env: Mapping[str, str], name: str, value: str) -> None:
    """Create or replace a SecureString. The value is on the command line only here, and it's
    redacted from any error."""
    if not value.strip():
        raise CommandError("The value is empty; nothing was stored.")
    run(
        ["aws", "ssm", "put-parameter", "--name", name,
         "--type", "SecureString", "--overwrite", "--value", value.strip()],
        env=env,
        redact=[value.strip()],
    )


def parameter_exists(run: Runner, env: Mapping[str, str], name: str) -> bool:
    found = run(
        ["aws", "ssm", "describe-parameters", "--parameter-filters", f"Key=Name,Values={name}",
         "--query", "Parameters[0].Name", "--output", "text"],
        env=env,
    ).stdout.strip()
    return found == name


def read_otlp_auth(run: Runner, env: Mapping[str, str], stage: str) -> str:
    return read_parameter(
        run, env, otlp_auth_parameter(stage), f"just store-grafana-token {stage}"
    )


def store_otlp_auth(run: Runner, env: Mapping[str, str], stage: str, value: str) -> None:
    if not value.strip():
        raise CommandError("The token is empty; nothing was stored.")
    store_parameter(run, env, otlp_auth_parameter(stage), value)


def otlp_auth_value(instance_id: str, token: str) -> str:
    """Grafana Cloud's OTLP basic-auth value: base64("<instanceID>:<token>")."""
    instance_id, token = instance_id.strip(), token.strip()
    if not instance_id.isdigit() or not token:
        raise CommandError("Enter the numeric Grafana instance ID and a non-empty token.")
    return base64.b64encode(f"{instance_id}:{token}".encode()).decode()
