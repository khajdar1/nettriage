"""Runtime settings, read from NETTRIAGE_* environment variables."""

import os
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Stage = Literal["local", "dev", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETTRIAGE_", frozen=True)

    stage: Stage = "local"
    version: str = "0.0.0-local"
    service_name: str = "nettriage-api"
    # Set by Terraform on the function (spec §5.5, §6.8). The parameters hold the Cognito app
    # client's settings (JSON), its client secret, and app_api's pooled database URL.
    runtime_table: str = ""
    oidc_parameter: str = ""
    oidc_secret_parameter: str = ""
    database_url_parameter: str = ""
    # The uploads bucket, and the SSM parameter that switches uploads on and off (spec §9.7).
    uploads_bucket: str = ""
    uploads_enabled_parameter: str = ""
    # The triage worker (Plan 5b): the AI kill switch's SSM parameter, and the model it calls,
    # on demand in the model's own Region (spec §8.3, §9.7).
    ai_enabled_parameter: str = ""
    bedrock_region: str = "eu-north-1"
    bedrock_model_id: str = ""
    # Where the analyze worker queues findings for an AI explanation (spec §4.2).
    triage_queue_url: str = ""
    # The ops function (Plan 7a): app_backup's direct database URL in SSM (its own
    # `database_url_parameter` holds app_ops's pooled one), the backups bucket, and the app's
    # CloudFront URL, which the probe fetches /api/health through.
    backup_database_url_parameter: str = ""
    backups_bucket: str = ""
    app_url: str = ""

    @property
    def running_in_lambda(self) -> bool:
        return "AWS_LAMBDA_FUNCTION_NAME" in os.environ

    @property
    def api_docs_enabled(self) -> bool:
        return self.stage in ("local", "dev")
