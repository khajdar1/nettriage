"""Liveness check. It never touches the database, so probes don't wake Neon."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from nettriage.entrypoints.api.dependencies import get_settings
from nettriage.platform.config import Settings

router = APIRouter()


class Health(BaseModel):
    status: str
    version: str


@router.get("/health")
def health(response: Response, settings: Annotated[Settings, Depends(get_settings)]) -> Health:
    response.headers["Cache-Control"] = "no-store"
    return Health(status="ok", version=settings.version)
