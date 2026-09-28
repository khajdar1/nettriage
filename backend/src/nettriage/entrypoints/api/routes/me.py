"""The signed-in user (spec §7): who they are, their memberships, and the CSRF token the SPA
sends back on state-changing requests."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.users import load_me
from nettriage.entrypoints.api.access import CurrentSession, unauthorized, unavailable
from nettriage.entrypoints.api.services import get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str | None


class MembershipOut(BaseModel):
    org_id: UUID
    name: str
    slug: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    csrf_token: str


@router.get("/me")
def me(request: Request, response: Response, session: CurrentSession) -> MeOut:
    try:
        found = load_me(get_services(request).database, session.user_id)
    except SQLAlchemyError:
        logger.exception("load_me_failed")
        raise unavailable("Your account") from None
    if found is None:
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    return MeOut(
        user=UserOut(id=found.user_id, email=found.email, display_name=found.display_name),
        memberships=[
            MembershipOut(org_id=m.org_id, name=m.name, slug=m.slug, role=m.role)
            for m in found.memberships
        ],
        csrf_token=session.csrf_token,
    )
