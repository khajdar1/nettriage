"""Seed users, organizations, memberships, invitations, uploads, findings and AI analyses for
integration tests. Seeding uses the superuser engine, which row-level security doesn't apply to."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True)
class Tenant:
    org_id: UUID
    owner_id: UUID
    invitation_id: UUID
    upload_id: UUID
    finding_id: UUID
    analysis_id: UUID


def add_user(connection: Connection, email: str | None = None) -> UUID:
    user_id = uuid7()
    connection.execute(
        text("INSERT INTO users (id, cognito_sub, email) VALUES (:id, :sub, :email)"),
        {"id": user_id, "sub": f"sub-{user_id}", "email": email or f"{user_id}@example.com"},
    )
    return user_id


def add_org(connection: Connection, created_by: UUID) -> UUID:
    org_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_by) "
            "VALUES (:id, :name, :slug, :created_by)"
        ),
        {"id": org_id, "name": "Org", "slug": f"org-{org_id.hex}", "created_by": created_by},
    )
    return org_id


def add_member(connection: Connection, org_id: UUID, user_id: UUID, role: str) -> None:
    connection.execute(
        text("INSERT INTO memberships (org_id, user_id, role) VALUES (:org, :user, :role)"),
        {"org": org_id, "user": user_id, "role": role},
    )


def add_invitation(
    connection: Connection, org_id: UUID, created_by: UUID, email: str, role: str = "viewer"
) -> UUID:
    invitation_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO invitations (id, org_id, email, role, token_hash, expires_at, created_by) "
            "VALUES (:id, :org, :email, :role, :hash, :expires, :created_by)"
        ),
        {
            "id": invitation_id,
            "org": org_id,
            "email": email,
            "role": role,
            "hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            "expires": datetime.now(UTC) + timedelta(days=7),
            "created_by": created_by,
        },
    )
    return invitation_id


def add_upload(
    connection: Connection, org_id: UUID, uploaded_by: UUID, status: str = "pending_upload"
) -> UUID:
    upload_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO uploads (id, org_id, uploaded_by, original_filename, s3_key, size_bytes, "
            "sha256, status) VALUES (:id, :org, :by, 'flows.log', :key, 1024, :sha256, :status)"
        ),
        {
            "id": upload_id,
            "org": org_id,
            "by": uploaded_by,
            "key": f"orgs/{org_id}/uploads/{upload_id}/raw",
            "sha256": "0" * 64,
            "status": status,
        },
    )
    return upload_id


def add_finding(
    connection: Connection,
    org_id: UUID,
    upload_id: UUID,
    severity: str = "high",
    detector: str = "port_scan",
) -> UUID:
    """A finding (a port scan unless told otherwise) with one evidence row, its detector
    technique and its created event."""
    finding_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO findings (id, org_id, upload_id, detector_id, detector_version, "
            "fingerprint, severity, title, src_ip, dst_ip, time_window) VALUES (:id, :org, "
            ":upload, :detector, 1, :fingerprint, :severity, 'Port scan from 203.0.113.9', "
            "'203.0.113.9', '10.0.0.5', tstzrange('2026-09-28 12:00+00', '2026-09-28 12:05+00'))"
        ),
        {
            "id": finding_id,
            "org": org_id,
            "upload": upload_id,
            "fingerprint": finding_id.hex * 2,
            "severity": severity,
            "detector": detector,
        },
    )
    connection.execute(
        text(
            "INSERT INTO finding_evidence (id, org_id, finding_id, src_ip, dst_ip, src_port, "
            "dst_port, protocol, packets, bytes, start_ts, end_ts, action, line_no) VALUES "
            "(:id, :org, :finding, '203.0.113.9', '10.0.0.5', 40000, 22, 6, 1, 40, "
            "'2026-09-28 12:00+00', '2026-09-28 12:00+00', 'REJECT', 2)"
        ),
        {"id": uuid7(), "org": org_id, "finding": finding_id},
    )
    connection.execute(
        text(
            "INSERT INTO finding_techniques (finding_id, technique_id, source, org_id) "
            "VALUES (:finding, 'T1595', 'detector', :org)"
        ),
        {"finding": finding_id, "org": org_id},
    )
    connection.execute(
        text(
            "INSERT INTO finding_events (id, org_id, finding_id, type) "
            "VALUES (:id, :org, :finding, 'created')"
        ),
        {"id": uuid7(), "org": org_id, "finding": finding_id},
    )
    return finding_id


def add_analysis(
    connection: Connection, org_id: UUID, finding_id: UUID, status: str = "succeeded"
) -> UUID:
    """An analysis of the finding by the fake model, with a minimal output when it succeeded."""
    analysis_id = uuid7()
    connection.execute(
        text(
            "INSERT INTO ai_analyses (id, org_id, finding_id, status, provider, model_id, "
            "prompt_version, output_schema_version, input_hash, output) VALUES (:id, :org, "
            ":finding, :status, 'fake', 'fake-triage', 'v1', 'v1', :hash, CAST(:output AS jsonb))"
        ),
        {
            "id": analysis_id,
            "org": org_id,
            "finding": finding_id,
            "status": status,
            "hash": analysis_id.hex * 2,
            "output": '{"summary": "A scan."}' if status == "succeeded" else None,
        },
    )
    return analysis_id


def add_usage(connection: Connection, org_id: UUID) -> None:
    """Today's AI usage for the org: one call (spec §5.2, Plan 5c)."""
    connection.execute(
        text(
            "INSERT INTO ai_usage (org_id, day, calls, input_tokens, output_tokens, cost_usd) "
            "VALUES (:org, (now() AT TIME ZONE 'UTC')::date, 1, 1800, 320, 0.000222)"
        ),
        {"org": org_id},
    )


def add_tenant(admin: Engine) -> Tenant:
    """An org with an owner, a pending invitation, an analyzed upload with a finding that has a
    succeeded AI analysis, and today's AI usage."""
    with admin.begin() as connection:
        owner = add_user(connection)
        org = add_org(connection, owner)
        add_member(connection, org, owner, "owner")
        invitation = add_invitation(connection, org, owner, f"invitee-{org.hex}@example.com")
        upload = add_upload(connection, org, owner, "analyzed")
        finding = add_finding(connection, org, upload)
        analysis = add_analysis(connection, org, finding)
        add_usage(connection, org)
    return Tenant(
        org_id=org,
        owner_id=owner,
        invitation_id=invitation,
        upload_id=upload,
        finding_id=finding,
        analysis_id=analysis,
    )
