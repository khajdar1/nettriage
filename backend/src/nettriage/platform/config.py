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

    @property
    def running_in_lambda(self) -> bool:
        return "AWS_LAMBDA_FUNCTION_NAME" in os.environ

    @property
    def api_docs_enabled(self) -> bool:
        return self.stage in ("local", "dev")
