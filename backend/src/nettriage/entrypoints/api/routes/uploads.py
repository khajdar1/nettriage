"""Uploads (spec §4.2, §7): register a file and get a presigned PUT for it, then list and read
the org's uploads. The file itself goes from the browser straight to S3."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid7

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from nettriage.adapters.idempotency import StoredResponse
from nettriage.adapters.uploads import create_upload, get_upload, list_uploads
from nettriage.application.rate_limits import POLICIES
from nettriage.application.uploads import s3_key
from nettriage.entrypoints.api.access import OrgContext, OrgMember, enforce
from nettriage.entrypoints.api.auditing import audit
from nettriage.entrypoints.api.cursors import decode_cursor, encode_cursor
from nettriage.entrypoints.api.idempotent import create_once, created_response
from nettriage.entrypoints.api.org_errors import org_rules
from nettriage.entrypoints.api.services import get_services
from nettriage.entrypoints.api.upload_schemas import (
    CreatedUploadOut,
    UploadIn,
    UploadOut,
    UploadsOut,
)
from nettriage.platform.trace_context import current_traceparent

router = APIRouter(prefix="/v1/orgs/{org_id}/uploads")


@router.post("", status_code=201, response_model=CreatedUploadOut)
def create(
    request: Request,
    org_id: UUID,
    body: UploadIn,
    org: Annotated[OrgContext, Depends(OrgMember("uploads:create"))],
) -> Response:
    """Register a file and get a presigned PUT for it, valid for 5 minutes. It counts against
    the org's 20 uploads a day; an `Idempotency-Key` makes a retry return the same upload."""
    services = get_services(request)
    if not services.uploads_switch.is_on():
        raise HTTPException(503, detail="Uploads are paused for now. Try again later.")

    def work() -> StoredResponse:
        enforce(request, POLICIES["uploads.org"], str(org.org_id), actor=org.user_id)
        upload_id = uuid7()
        key = s3_key(org.org_id, upload_id)
        with org_rules(request, "Creating an upload", org=org, permission="uploads:create"):
            upload = create_upload(
                services.database,
                org.org_id,
                user_id=org.user_id,
                upload_id=upload_id,
                filename=body.filename,
                size_bytes=body.size_bytes,
                sha256=body.sha256,
                s3_key=key,
            )
        put = services.upload_storage.presign_put(
            key=key,
            size_bytes=upload.size_bytes,
            sha256=upload.sha256,
            traceparent=current_traceparent(),
            now=services.clock(),
        )
        audit(
            request,
            action="upload.created",
            outcome="success",
            actor_user_id=org.user_id,
            org_id=org.org_id,
            target_type="upload",
            target_id=str(upload.id),
            details={"size_bytes": upload.size_bytes},
        )
        created = CreatedUploadOut(
            upload=UploadOut.of(upload),
            upload_url=put.url,
            upload_headers=put.headers,
            expires_at=put.expires_at,
        )
        return StoredResponse(status=201, body=created.model_dump(mode="json"))

    stored = create_once(request, org.user_id, body, "Creating an upload", work)
    upload_id = stored.body["upload"]["id"]
    return created_response(stored, f"/api/v1/orgs/{org.org_id}/uploads/{upload_id}")


@router.get("")
def uploads(
    request: Request,
    org_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("uploads:read"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> UploadsOut:
    """The org's uploads, newest first, a page at a time."""
    before = decode_cursor(cursor) if cursor else None
    with org_rules(request, "The upload list"):
        found = list_uploads(
            get_services(request).database, org.org_id, org.user_id, limit=limit + 1, before=before
        )
    page = found[:limit]
    more = len(found) > limit
    return UploadsOut(
        uploads=[UploadOut.of(upload) for upload in page],
        next_cursor=encode_cursor(page[-1].created_at, page[-1].id) if more else None,
    )


@router.get("/{upload_id}")
def upload(
    request: Request,
    org_id: UUID,
    upload_id: UUID,
    org: Annotated[OrgContext, Depends(OrgMember("uploads:read"))],
) -> UploadOut:
    """One upload: its status and, once analyzed, its statistics."""
    with org_rules(request, "This upload"):
        return UploadOut.of(
            get_upload(get_services(request).database, org.org_id, org.user_id, upload_id)
        )
