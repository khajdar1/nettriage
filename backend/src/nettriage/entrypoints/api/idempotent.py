"""`Idempotency-Key` on POSTs that create something (spec §7): a retry with the same key and the
same request gets the first response instead of creating a second one. Keys are kept for 24
hours per user (adapters/idempotency.py).

A request's hash covers its method and path as well as its body, so a key used to create an org
can't replay that org as an upload, and a key used in one org can't in another."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from nettriage.adapters.idempotency import (
    IdempotencyInProgress,
    IdempotencyMismatch,
    StoredResponse,
)
from nettriage.entrypoints.api.access import unavailable
from nettriage.entrypoints.api.services import get_services

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")


def request_hash(method: str, path: str, body: BaseModel) -> str:
    canonical = json.dumps(
        {"method": method, "path": path, "body": body.model_dump(mode="json")}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_once(
    request: Request,
    user_id: UUID,
    body: BaseModel,
    what: str,
    work: Callable[[], StoredResponse],
) -> StoredResponse:
    """Run `work` once per `Idempotency-Key`. Without a key it simply runs. A failure forgets
    the key, so the client can retry with it; a key that can't be read refuses the request
    (503) rather than risk creating twice."""
    services = get_services(request)
    key = request.headers.get("idempotency-key")
    if key is None:
        return work()
    if not IDEMPOTENCY_KEY.fullmatch(key):
        raise HTTPException(
            422, detail="Idempotency-Key must be 1 to 100 letters, digits, dashes or underscores."
        )
    fingerprint = request_hash(request.method, request.url.path, body)
    try:
        stored = services.idempotency.begin(user_id, key, fingerprint, services.clock())
    except IdempotencyMismatch:
        raise HTTPException(
            422, detail="This Idempotency-Key was already used for a different request."
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            409, detail="A request with this Idempotency-Key is still running; retry shortly."
        ) from None
    except BotoCoreError, ClientError:
        logger.exception("idempotency_read_failed")
        raise unavailable(what) from None
    if stored is not None:
        return stored
    try:
        created = work()
    except Exception:
        _forget(request, user_id, key)
        raise
    try:
        services.idempotency.finish(user_id, key, created, services.clock())
    except BotoCoreError, ClientError:
        logger.warning("idempotency_write_failed")
    return created


def created_response(stored: StoredResponse, location: str) -> JSONResponse:
    return JSONResponse(stored.body, status_code=stored.status, headers={"Location": location})


def _forget(request: Request, user_id: UUID, key: str) -> None:
    try:
        get_services(request).idempotency.abandon(user_id, key)
    except BotoCoreError, ClientError:
        logger.warning("idempotency_abandon_failed")
