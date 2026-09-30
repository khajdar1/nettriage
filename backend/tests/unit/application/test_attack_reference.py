"""The shipped ATT&CK reference data (spec §5.2): every technique a detector can name, from
ATT&CK 19.2, with MITRE's notice."""

import re

from nettriage.domain.detection.engine import DETECTORS
from nettriage.reference import attack_reference

TECHNIQUE_ID = re.compile(r"T\d{4}(\.\d{3})?")


def test_it_is_attack_19_2_with_mitres_notice_and_license() -> None:
    reference = attack_reference()

    assert reference.version == "19.2"
    assert reference.notice.startswith("Copyright 2015-2026, The MITRE Corporation.")
    assert "non-exclusive, royalty-free license to use ATT&CK" in reference.license


def test_every_technique_a_detector_can_name_is_there_with_its_parent() -> None:
    known = {technique.id: technique for technique in attack_reference().techniques}
    named = {technique for detector in DETECTORS for technique in detector.candidate_techniques}

    assert named <= known.keys()
    for technique in known.values():
        assert TECHNIQUE_ID.fullmatch(technique.id)
        path = technique.id.replace(".", "/")
        assert technique.url == f"https://attack.mitre.org/techniques/{path}"
        assert technique.tactics
        assert "(Citation:" not in technique.description
        if technique.is_subtechnique:
            assert technique.parent_id == technique.id.split(".")[0]
            assert technique.parent_id in known
