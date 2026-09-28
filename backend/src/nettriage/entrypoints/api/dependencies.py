"""FastAPI dependencies shared by routes."""

from typing import cast

from fastapi import Request

from nettriage.platform.config import Settings


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)
