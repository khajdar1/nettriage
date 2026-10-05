"""Cursors for lists that page newest first (spec §7): an opaque base64url string holding the
last item's `(created_at, id)`, which the next page continues after. Findings sorted most severe
first (Plan 6b) lead with the last item's severity: `(severity, created_at, id)`."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException


def encode_cursor(created_at: datetime, item_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(item_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created_at, item_id = json.loads(raw)
        return datetime.fromisoformat(created_at), UUID(item_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(
            422, detail="The cursor isn't valid; start from the first page."
        ) from error


SEVERITIES = ("low", "medium", "high", "critical")


def encode_severity_cursor(severity: str, created_at: datetime, item_id: UUID) -> str:
    """A cursor for a list sorted most severe first: the last item's severity leads."""
    raw = json.dumps([severity, created_at.isoformat(), str(item_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_severity_cursor(cursor: str) -> tuple[str, datetime, UUID]:
    """A cursor made for the newest-first order has no severity, so it is refused here."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        severity, created_at, item_id = json.loads(raw)
        if severity not in SEVERITIES:
            raise ValueError(severity)
        return severity, datetime.fromisoformat(created_at), UUID(item_id)
    except (ValueError, TypeError) as error:
        raise HTTPException(
            422, detail="The cursor isn't valid; start from the first page."
        ) from error
