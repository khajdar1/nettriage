"""ATT&CK techniques (spec §7): reference data for anyone signed in, with MITRE's notice."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.attack_techniques import get_technique
from nettriage.entrypoints.api.access import CurrentSession, unavailable
from nettriage.entrypoints.api.finding_schemas import TechniqueOut
from nettriage.entrypoints.api.services import get_services
from nettriage.reference import attack_reference

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/attack-techniques")

TECHNIQUE_ID = re.compile(r"T\d{4}(\.\d{3})?")


@router.get("/{technique_id}")
def technique(request: Request, technique_id: str, session: CurrentSession) -> TechniqueOut:
    """One ATT&CK technique (`T1046`, `T1110.001`) from the version NetTriage ships."""
    if not TECHNIQUE_ID.fullmatch(technique_id):
        raise HTTPException(404, detail="No such ATT&CK technique.")
    try:
        found = get_technique(get_services(request).database, session.user_id, technique_id)
    except SQLAlchemyError:
        logger.exception("technique_read_failed")
        raise unavailable("The ATT&CK reference") from None
    if found is None:
        raise HTTPException(404, detail="No such ATT&CK technique.")
    return TechniqueOut.of(found, attack_reference().notice)
