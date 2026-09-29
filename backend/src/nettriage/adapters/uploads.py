"""Uploads in Postgres (spec §5.2, §7): one row per file, created as `pending_upload` when a
member asks to upload one. Each function is one transaction as `app_api`, so row-level security
applies throughout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, Row, text

from nettriage.adapters.organizations import lock_org_for
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import NotFound, require
from nettriage.application.uploads import UploadStatus

_COLUMNS = (
    "id, org_id, uploaded_by, original_filename, size_bytes, sha256, status, failure_reason, "
    "rows_parsed, rows_rejected, rejected_samples, findings_truncated, "
    "lower(flow_time_range) AS flow_start, upper(flow_time_range) AS flow_end, processed_at, "
    "created_at"
)


@dataclass(frozen=True)
class Upload:
    id: UUID
    org_id: UUID
    uploaded_by: UUID
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
    processed_at: datetime | None
    created_at: datetime


def create_upload(
    engine: Engine,
    org_id: UUID,
    *,
    user_id: UUID,
    upload_id: UUID,
    filename: str,
    size_bytes: int,
    sha256: str,
    s3_key: str,
) -> Upload:
    """A new `pending_upload` row. Like every change, it decides with the caller's role as it
    is under the org's lock (Plan 3c)."""
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        require(lock_org_for(connection, org_id, user_id), "uploads:create")
        row = connection.execute(
            text(
                "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, "  # noqa: S608
                "size_bytes, sha256) VALUES (:id, :org, :user, :filename, :key, :size, :sha256) "
                f"RETURNING {_COLUMNS}"
            ),
            {
                "id": upload_id,
                "org": org_id,
                "user": user_id,
                "filename": filename,
                "key": s3_key,
                "size": size_bytes,
                "sha256": sha256,
            },
        ).one()
    return _upload(row)


def get_upload(engine: Engine, org_id: UUID, user_id: UUID, upload_id: UUID) -> Upload:
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        row = connection.execute(
            text(f"SELECT {_COLUMNS} FROM uploads WHERE org_id = :org AND id = :id"),  # noqa: S608
            {"org": org_id, "id": upload_id},
        ).one_or_none()
    if row is None:
        raise NotFound("No such upload.")
    return _upload(row)


def list_uploads(
    engine: Engine,
    org_id: UUID,
    user_id: UUID,
    *,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> list[Upload]:
    """The org's uploads, newest first; `before` continues after the last upload of a page."""
    before_at, before_id = before or (None, None)
    with tenant_transaction(engine, org_id=org_id, user_id=user_id) as connection:
        rows = connection.execute(
            text(
                f"SELECT {_COLUMNS} FROM uploads WHERE org_id = :org "  # noqa: S608
                "AND (CAST(:before_at AS timestamptz) IS NULL OR (created_at, id) < "
                "(CAST(:before_at AS timestamptz), CAST(:before_id AS uuid))) "
                "ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            {"org": org_id, "before_at": before_at, "before_id": before_id, "limit": limit},
        ).all()
    return [_upload(row) for row in rows]


def _upload(row: Row[Any]) -> Upload:
    return Upload(
        id=row.id,
        org_id=row.org_id,
        uploaded_by=row.uploaded_by,
        original_filename=row.original_filename,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        status=row.status,
        failure_reason=row.failure_reason,
        rows_parsed=row.rows_parsed,
        rows_rejected=row.rows_rejected,
        rejected_samples=row.rejected_samples,
        findings_truncated=row.findings_truncated,
        flow_start=row.flow_start,
        flow_end=row.flow_end,
        processed_at=row.processed_at,
        created_at=row.created_at,
    )
