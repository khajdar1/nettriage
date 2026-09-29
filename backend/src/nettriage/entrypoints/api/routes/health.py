"""Liveness check. It never touches the database, so probes don't wake Neon. It is public, and
rate-limited per client IP like every public route (spec §6.5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from nettriage.entrypoints.api.access import Public
from nettriage.entrypoints.api.dependencies import get_settings
from nettriage.platform.config import Settings

router = APIRouter()


class Health(BaseModel):
    status: str
    version: str


# A limited health check only counts a metric: an audit row would wake Neon, and the probe
# must never touch the database.
@router.get("/health", dependencies=[Depends(Public("public.ip", audit_limits=False))])
def health(response: Response, settings: Annotated[Settings, Depends(get_settings)]) -> Health:
    response.headers["Cache-Control"] = "no-store"
    return Health(status="ok", version=settings.version)
