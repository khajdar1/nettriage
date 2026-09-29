"""Request and response bodies for uploads (spec §7). The request model forbids fields it doesn't
declare, so a client can't set a status or results (OWASP API3)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from nettriage.adapters.uploads import Upload
from nettriage.application.uploads import MAX_FILENAME, MAX_UPLOAD_BYTES, UploadStatus
from nettriage.entrypoints.api.schemas import Strict

# Shown back to the org's members only; it never becomes part of an S3 key, a log or an audit
# event. No control characters (line breaks, tabs, NUL).
FileName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_FILENAME, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$", to_lower=True)]


class UploadIn(Strict):
    filename: FileName
    size_bytes: Annotated[int, Field(ge=1, le=MAX_UPLOAD_BYTES)]
    sha256: Sha256Hex


class UploadOut(BaseModel):
    id: UUID
    original_filename: str
    size_bytes: int
    sha256: str
    status: UploadStatus
    failure_reason: str | None
    rows_parsed: int | None
    rows_rejected: int | None
    rejected_samples: list[dict[str, Any]]
    findings_truncated: int
    flow_start: datetime | None
    flow_end: datetime | None
    uploaded_by: UUID
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def of(cls, upload: Upload) -> UploadOut:
        return cls(
            id=upload.id,
            original_filename=upload.original_filename,
            size_bytes=upload.size_bytes,
            sha256=upload.sha256,
            status=upload.status,
            failure_reason=upload.failure_reason,
            rows_parsed=upload.rows_parsed,
            rows_rejected=upload.rows_rejected,
            rejected_samples=upload.rejected_samples,
            findings_truncated=upload.findings_truncated,
            flow_start=upload.flow_start,
            flow_end=upload.flow_end,
            uploaded_by=upload.uploaded_by,
            created_at=upload.created_at,
            processed_at=upload.processed_at,
        )


class CreatedUploadOut(BaseModel):
    upload: UploadOut
    # PUT the file here within 5 minutes, sending these headers; the browser adds
    # Content-Length, which must equal size_bytes.
    upload_url: str
    upload_headers: dict[str, str]
    expires_at: datetime


class UploadsOut(BaseModel):
    uploads: list[UploadOut]
    next_cursor: str | None
