"""Turn the owner's short-lived `aws login` session into environment credentials for one run."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from tools.deploy.config import REGION
from tools.deploy.runner import CommandError, Runner

# Credential, profile and Region variables: dropped so a stale session or a wrong profile from
# the owner's own shell can never leak into a command; the returned env sets AWS_PROFILE and
# both Region variables itself.
_AWS_VARS_TO_DROP = frozenset(
    {
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
        "AWS_CREDENTIAL_EXPIRATION", "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_REGION", "AWS_DEFAULT_REGION",
    }
)
# Terraform overrides: TF_LOG/TF_LOG_PATH would write request bodies (the Grafana token) to disk
# or the console, and TF_CLI_ARGS(_*)=-auto-approve would skip the owner's confirmation.
# TF_PLUGIN_CACHE_DIR and TF_VAR_* are kept.
_TF_VARS_TO_DROP = frozenset({"TF_LOG", "TF_LOG_PATH", "TF_LOG_CORE", "TF_LOG_PROVIDER", "TF_CLI_ARGS", "TF_WORKSPACE"})


def _is_dropped(key: str) -> bool:
    if key in _AWS_VARS_TO_DROP or key in _TF_VARS_TO_DROP:
        return True
    return key.startswith("TF_CLI_ARGS_")


def _base_env() -> dict[str, str]:
    """The environment every command this tool runs gets: this process's environment minus the
    credential/profile/Region and Terraform-override variables above (everything else, including
    AWS_CA_BUNDLE and a custom AWS_CONFIG_FILE or AWS_SHARED_CREDENTIALS_FILE, passes through)."""
    return {key: value for key, value in os.environ.items() if not _is_dropped(key)}


def aws_env(run: Runner, profile: str) -> dict[str, str]:
    """This process's environment plus a profile that refreshes credentials as they expire.

    The owner's short-lived `aws login` session is validated once (malformed output or
    long-lived keys are refused with a sign-in hint). A helper profile "<profile>-tools" is
    then ensured in the owner's AWS config, with a `credential_process` that re-runs
    `aws configure export-credentials` on every AWS/Terraform call. `aws login` sessions last
    only 15 minutes, and a first deploy (CloudFront creation, the owner reading the plan) can
    outlast one, so the returned environment names that profile instead of exporting static
    keys: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and AWS_SESSION_TOKEN never appear in it.
    """
    base = _base_env()
    try:
        raw = run(
            ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
            env=base,
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
    if not creds.get("SessionToken"):
        raise CommandError(
            f"Profile '{profile}' uses long-lived access keys. Use a short-lived session instead: "
            f"aws login --profile {profile}"
        )
    _ensure_tools_profile(run, profile, base)
    env = dict(base)
    env.update(
        {
            "AWS_PROFILE": f"{profile}-tools",
            "AWS_REGION": REGION,
            "AWS_DEFAULT_REGION": REGION,
        }
    )
    return env


def _ensure_tools_profile(run: Runner, profile: str, base: Mapping[str, str]) -> None:
    """Create or fix the helper profile whose credential_process refreshes the session."""
    tools_profile = f"{profile}-tools"
    expected = f"aws configure export-credentials --profile {profile} --format process"
    current = run(
        ["aws", "configure", "get", "credential_process", "--profile", tools_profile],
        env=base, check=False,
    )
    if current.returncode == 0 and current.stdout.strip() == expected:
        return
    run(["aws", "configure", "set", "credential_process", expected, "--profile", tools_profile], env=base)
    run(["aws", "configure", "set", "region", REGION, "--profile", tools_profile], env=base)
    print(f"Created AWS profile '{tools_profile}', which refreshes your '{profile}' session for long commands.")


def account_id(run: Runner, env: Mapping[str, str]) -> str:
    return run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"], env=env
    ).stdout.strip()
