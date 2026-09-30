"""The analyze worker's side of Postgres (spec §4.2, §8.6), as `app_analyze`: claim an upload,
then store its results or its failure, each in one transaction.

An upload moves pending_upload → processing → analyzed or failed. SQS may deliver the same event
twice, or again after a crash, so:
- claiming accepts pending_upload or processing (a retry after a crash), and nothing else;
- storing locks the upload and stores nothing unless it's still processing, so a duplicate that
  finishes second changes nothing. The unique `(org_id, upload_id, fingerprint)` is the backstop.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.analysis import Analysis, UploadKey
from nettriage.domain.detection.model import Finding


@dataclass(frozen=True)
class ClaimedUpload:
    org_id: UUID
    upload_id: UUID
    size_bytes: int
    sha256: str


def claim_upload(engine: Engine, key: UploadKey) -> ClaimedUpload | None:
    """Mark the upload `processing`. None if the key names no upload of that org, or its upload
    is already analyzed, failed or expired."""
    with tenant_transaction(engine, org_id=key.org_id) as connection:
        row = connection.execute(
            text(
                "UPDATE uploads SET status = 'processing' WHERE org_id = :org AND id = :id "
                "AND s3_key = :key AND status IN ('pending_upload', 'processing') "
                "RETURNING size_bytes, sha256"
            ),
            {"org": key.org_id, "id": key.upload_id, "key": key.key},
        ).one_or_none()
    if row is None:
        return None
    return ClaimedUpload(
        org_id=key.org_id, upload_id=key.upload_id, size_bytes=row.size_bytes, sha256=row.sha256
    )


def store_analysis(
    engine: Engine, upload: ClaimedUpload, analysis: Analysis, now: datetime
) -> bool:
    """Store the findings and mark the upload analyzed. False if another delivery already
    finished it."""
    with tenant_transaction(engine, org_id=upload.org_id) as connection:
        if not _still_processing(connection, upload):
            return False
        for finding in analysis.findings:
            _insert_finding(connection, upload, finding)
        connection.execute(
            text(
                "UPDATE uploads SET status = 'analyzed', failure_reason = NULL, "
                "rows_parsed = :parsed, rows_rejected = :rejected, "
                "rejected_samples = CAST(:samples AS jsonb), findings_truncated = :truncated, "
                "flow_time_range = CASE WHEN CAST(:start AS timestamptz) IS NULL THEN NULL "
                "ELSE tstzrange(CAST(:start AS timestamptz), CAST(:end AS timestamptz), '[]') END, "
                "processed_at = :now WHERE org_id = :org AND id = :id"
            ),
            {
                "parsed": analysis.rows_parsed,
                "rejected": analysis.rows_rejected,
                "samples": json.dumps([asdict(sample) for sample in analysis.rejected_samples]),
                "truncated": analysis.findings_truncated,
                "start": analysis.flow_start,
                "end": analysis.flow_end,
                "now": now,
                "org": upload.org_id,
                "id": upload.upload_id,
            },
        )
    return True


def fail_upload(engine: Engine, upload: ClaimedUpload, reason: str, now: datetime) -> bool:
    """Mark the upload failed with a readable reason. False if it's no longer processing."""
    with tenant_transaction(engine, org_id=upload.org_id) as connection:
        failed = connection.execute(
            text(
                "UPDATE uploads SET status = 'failed', failure_reason = :reason, "
                "processed_at = :now WHERE org_id = :org AND id = :id AND status = 'processing'"
            ),
            {"reason": reason[:500], "now": now, "org": upload.org_id, "id": upload.upload_id},
        ).rowcount
    return failed == 1


def _still_processing(connection: Connection, upload: ClaimedUpload) -> bool:
    status = connection.execute(
        text("SELECT status FROM uploads WHERE org_id = :org AND id = :id FOR UPDATE"),
        {"org": upload.org_id, "id": upload.upload_id},
    ).scalar_one_or_none()
    return bool(status == "processing")


def _insert_finding(connection: Connection, upload: ClaimedUpload, finding: Finding) -> None:
    finding_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO findings (id, org_id, upload_id, detector_id, detector_version, "
            "fingerprint, severity, title, src_ip, dst_ip, dst_port, protocol, time_window, "
            "metrics) VALUES (:id, :org, :upload, :detector, :version, :fingerprint, :severity, "
            ":title, :src, :dst, :port, :protocol, "
            "tstzrange(CAST(:start AS timestamptz), CAST(:end AS timestamptz), '[]'), "
            "CAST(:metrics AS jsonb))"
        ),
        {
            "id": finding_id,
            "org": upload.org_id,
            "upload": upload.upload_id,
            "detector": finding.detector_id,
            "version": finding.detector_version,
            "fingerprint": finding.fingerprint,
            "severity": finding.severity.value,
            "title": finding.title,
            "src": str(finding.src_ip),
            "dst": str(finding.dst_ip) if finding.dst_ip is not None else None,
            "port": finding.dst_port,
            "protocol": finding.protocol,
            "start": finding.window_start,
            "end": finding.window_end,
            "metrics": json.dumps(dict(finding.metrics)),
        },
    )
    if finding.evidence:
        connection.execute(
            text(
                "INSERT INTO finding_evidence (id, org_id, finding_id, src_ip, dst_ip, src_port, "
                "dst_port, protocol, packets, bytes, start_ts, end_ts, action, line_no) VALUES "
                "(:id, :org, :finding, :src, :dst, :src_port, :dst_port, :protocol, :packets, "
                ":bytes, :start, :end, :action, :line)"
            ),
            [
                {
                    "id": uuid7(),
                    "org": upload.org_id,
                    "finding": finding_id,
                    "src": str(flow.src_ip),
                    "dst": str(flow.dst_ip),
                    "src_port": flow.src_port,
                    "dst_port": flow.dst_port,
                    "protocol": flow.protocol,
                    "packets": flow.packets,
                    "bytes": flow.bytes,
                    "start": flow.start,
                    "end": flow.end,
                    "action": flow.action,
                    "line": flow.line_no,
                }
                for flow in finding.evidence
            ],
        )
    if finding.candidate_techniques:
        connection.execute(
            text(
                "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
                "VALUES (:finding, :technique, 'detector', :org)"
            ),
            [
                {"finding": finding_id, "technique": technique, "org": upload.org_id}
                for technique in finding.candidate_techniques
            ],
        )
    connection.execute(
        text(
            "INSERT INTO finding_events (id, org_id, finding_id, type, payload) "
            "VALUES (:id, :org, :finding, 'created', CAST(:payload AS jsonb))"
        ),
        {
            "id": uuid7(),
            "org": upload.org_id,
            "finding": finding_id,
            "payload": json.dumps({"detector": finding.detector_id}),
        },
    )
