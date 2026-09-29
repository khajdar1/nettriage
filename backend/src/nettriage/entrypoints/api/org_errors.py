"""How the organization rules' refusals become HTTP answers (spec §6.4, §7), in one place.
An escalation attempt is a denial like any other, so it's recorded as `authz.denied`."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.application.organizations import (
    ConfirmationMismatch,
    Forbidden,
    InvitationInvalid,
    NotFound,
    OrgRuleError,
    WrongEmail,
)
from nettriage.entrypoints.api.access import OrgContext, deny, forbidden, unavailable

logger = logging.getLogger(__name__)


@contextmanager
def org_rules(
    request: Request, what: str, *, org: OrgContext | None = None, permission: str = ""
) -> Iterator[None]:
    """Run organization work, answering its refusals and outages as HTTP errors:
    - Forbidden (no escalation) is 403, and recorded as `authz.denied` for `permission`;
    - NotFound and InvitationInvalid are 404; WrongEmail is 403;
    - ConfirmationMismatch is 422; the rest (last owner, conflicts, quotas) are 409;
    - a database outage is 503."""
    try:
        yield
    except Forbidden as error:
        if org is not None:
            deny(
                request,
                org.user_id,
                permission,
                org_id=org.org_id,
                target=org.org_id,
                reason="escalation",
            )
        raise forbidden(str(error)) from None
    except (NotFound, InvitationInvalid) as error:
        raise HTTPException(404, detail=str(error)) from None
    except WrongEmail as error:
        raise HTTPException(403, detail=str(error)) from None
    except ConfirmationMismatch as error:
        raise HTTPException(422, detail=str(error)) from None
    except OrgRuleError as error:
        raise HTTPException(409, detail=str(error)) from None
    except SQLAlchemyError:
        logger.exception("organization_work_failed", extra={"what": what})
        raise unavailable(what) from None
