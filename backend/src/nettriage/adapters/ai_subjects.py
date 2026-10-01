"""Reading a finding for the model (spec §8.3), as `app_triage` in the finding's org: its typed
fields, its evidence and its detector's candidate techniques."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.ai_input import Candidate, EvidenceRow, TriageSubject
from nettriage.application.organizations import NotFound


def load_subject(engine: Engine, org_id: UUID, finding_id: UUID) -> TriageSubject:
    with tenant_transaction(engine, org_id=org_id) as connection:
        finding = connection.execute(
            text(
                "SELECT f.detector_id, f.detector_version, d.name AS detector_name, f.severity, "
                "f.metrics, host(f.src_ip) AS src_ip, host(f.dst_ip) AS dst_ip, f.dst_port, "
                "f.protocol, lower(f.time_window) AS window_start, "
                "upper(f.time_window) AS window_end FROM findings f "
                "JOIN detectors d ON d.id = f.detector_id WHERE f.org_id = :org AND f.id = :id"
            ),
            {"org": org_id, "id": finding_id},
        ).one_or_none()
        if finding is None:
            raise NotFound("No such finding.")
        evidence = connection.execute(
            text(
                "SELECT host(src_ip) AS src_ip, host(dst_ip) AS dst_ip, src_port, dst_port, "
                "protocol, packets, bytes, start_ts, end_ts, action FROM finding_evidence "
                "WHERE org_id = :org AND finding_id = :id ORDER BY start_ts, line_no"
            ),
            {"org": org_id, "id": finding_id},
        ).all()
        candidates = connection.execute(
            text(
                "SELECT t.id, t.name, t.description FROM finding_techniques ft "
                "JOIN attack_techniques t ON t.id = ft.technique_id "
                "WHERE ft.org_id = :org AND ft.finding_id = :id AND ft.source = 'detector' "
                "ORDER BY t.id"
            ),
            {"org": org_id, "id": finding_id},
        ).all()
    return TriageSubject(
        org_id=org_id,
        finding_id=finding_id,
        detector_id=finding.detector_id,
        detector_version=finding.detector_version,
        detector_name=finding.detector_name,
        severity=finding.severity,
        metrics=finding.metrics,
        src_ip=finding.src_ip,
        dst_ip=finding.dst_ip,
        dst_port=finding.dst_port,
        protocol=finding.protocol,
        window_start=finding.window_start,
        window_end=finding.window_end,
        evidence=tuple(
            EvidenceRow(
                src_ip=row.src_ip,
                dst_ip=row.dst_ip,
                src_port=row.src_port,
                dst_port=row.dst_port,
                protocol=row.protocol,
                packets=row.packets,
                bytes=row.bytes,
                start=row.start_ts,
                end=row.end_ts,
                action=row.action,
            )
            for row in evidence
        ),
        candidates=tuple(
            Candidate(id=row.id, name=row.name, description=row.description) for row in candidates
        ),
    )
