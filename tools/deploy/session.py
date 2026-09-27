"""Turn the owner's short-lived `aws login` session into environment credentials for one run."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from tools.deploy.config import REGION
from tools.deploy.runner import CommandError, Runner


def aws_env(run: Runner, profile: str) -> dict[str, str]:
    """This process's environment plus the profile's temporary credentials, in eu-north-1.

    Inherited AWS_* variables are dropped, so only the owner's session reaches AWS. Profiles
    holding long-lived access keys are refused (spec §6.8: no long-lived keys).
    """
    try:
        raw = run(
            ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"]
        ).stdout
        creds = json.loads(raw)
    except (CommandError, json.JSONDecodeError) as exc:
        raise CommandError(
            f"No usable AWS session for profile '{profile}'. Sign in with: aws login --profile {profile}"
        ) from exc
    # Validate that the parsed output is a dict with required non-empty keys.
    if (
        not isinstance(creds, dict)
        or not creds.get("AccessKeyId")
        or not creds.get("SecretAccessKey")
    ):
        raise CommandError(
            f"No usable AWS session for profile '{profile}'. Sign in with: aws login --profile {profile}"
        )
    session_token = creds.get("SessionToken")
    if not session_token:
        raise CommandError(
            f"Profile '{profile}' uses long-lived access keys. Use a short-lived session instead: "
            f"aws login --profile {profile}"
        )
    env = {key: value for key, value in os.environ.items() if not key.startswith("AWS_")}
    env.update(
        {
            "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
            "AWS_SESSION_TOKEN": session_token,
            "AWS_REGION": REGION,
            "AWS_DEFAULT_REGION": REGION,
        }
    )
    return env


def account_id(run: Runner, env: Mapping[str, str]) -> str:
    return run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], env=env
    ).stdout.strip()
