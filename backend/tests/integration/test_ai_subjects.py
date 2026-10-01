"""Reading a finding for the model (spec §8.3), as `app_triage`."""

import pytest
from conftest import Database
from tenantdata import add_tenant

from nettriage.adapters.ai_subjects import load_subject
from nettriage.application.organizations import NotFound


def test_a_finding_is_read_with_its_evidence_and_candidate_techniques(database: Database) -> None:
    tenant = add_tenant(database.admin)

    subject = load_subject(database.app_triage, tenant.org_id, tenant.finding_id)

    assert (subject.detector_id, subject.detector_name, subject.severity) == (
        "port_scan",
        "Port scan",
        "high",
    )
    assert (subject.src_ip, subject.dst_ip) == ("203.0.113.9", "10.0.0.5")
    assert [(row.dst_port, row.action) for row in subject.evidence] == [(22, "REJECT")]
    assert [(c.id, c.name) for c in subject.candidates] == [("T1595", "Active Scanning")]
    assert subject.candidates[0].description


def test_a_finding_of_another_org_is_not_found(database: Database) -> None:
    mine, theirs = add_tenant(database.admin), add_tenant(database.admin)

    with pytest.raises(NotFound):
        load_subject(database.app_triage, mine.org_id, theirs.finding_id)
