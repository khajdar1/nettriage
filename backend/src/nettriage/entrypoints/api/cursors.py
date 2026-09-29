"""Cursors for lists that page newest first (spec §7): an opaque base64url string holding the
last item's `(created_at, id)`, which the next page continues after."""

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
