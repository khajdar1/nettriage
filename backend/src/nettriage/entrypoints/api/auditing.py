"""Writing audit events from a request: who, from where, and which trace."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.audit_log import record
from nettriage.application.audit import AuditEvent, Outcome
from nettriage.application.rate_limits import viewer_ip
from nettriage.entrypoints.api.services import get_services
from nettriage.platform.trace_context import current_trace_id

logger = logging.getLogger(__name__)

VIEWER_ADDRESS = "cloudfront-viewer-address"
# CloudFront's ID for the request; it also appears in CloudFront's own logs.
REQUEST_ID = "x-amz-cf-id"


def audit(
    request: Request,
    *,
    action: str,
    outcome: Outcome,
    actor_user_id: UUID | None = None,
    org_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    """Append an audit event. A failed write is logged, not raised: the request it describes
    has already happened."""
    event = AuditEvent(
        action=action,
        outcome=outcome,
        actor_type="user" if actor_user_id else "anonymous",
        actor_user_id=actor_user_id,
        org_id=org_id,
        target_type=target_type,
        target_id=target_id,
        ip=viewer_ip(request.headers.get(VIEWER_ADDRESS)),
        user_agent=request.headers.get("user-agent"),
        request_id=request.headers.get(REQUEST_ID),
        trace_id=current_trace_id(),
        details=details or {},
    )
    try:
        record(get_services(request).database, event)
    except SQLAlchemyError:
        logger.exception("audit_write_failed", extra={"action": action})
