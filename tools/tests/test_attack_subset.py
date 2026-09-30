"""Extracting the detectors' ATT&CK techniques from MITRE's STIX bundle (spec §5.2)."""

from typing import Any

import pytest

from tools.attack_subset import SubsetError, extract, wanted_ids

NOTICE = "Copyright 2015-2026, The MITRE Corporation."


def technique(external_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "attack-pattern",
        "id": f"attack-pattern--{external_id}",
        "name": name,
        "description": "Adversaries may scan.(Citation: Some Report)  More text.(Citation: Other)",
        "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "reconnaissance"}],
        "external_references": [
            {
                "source_name": "mitre-attack",
                "external_id": external_id,
                "url": f"https://attack.mitre.org/techniques/{external_id.replace('.', '/')}",
            }
        ],
        "x_mitre_is_subtechnique": "." in external_id,
        **extra,
    }


def bundle(*objects: dict[str, Any], version: str = "19.2") -> dict[str, Any]:
    return {
        "objects": [
            {"type": "x-mitre-collection", "x_mitre_version": version},
            {"type": "marking-definition", "definition_type": "statement",
             "definition": {"statement": NOTICE}},
            *objects,
        ]
    }


def test_the_wanted_techniques_are_the_detectors_candidates_and_their_parents() -> None:
    wanted = wanted_ids()

    assert {"T1595", "T1595.001", "T1046", "T1110.003", "T1021", "T1021.004", "T1567"} <= wanted
    assert all(technique.split(".")[0] in wanted for technique in wanted)


def test_a_technique_keeps_its_names_tactics_and_link_without_citations() -> None:
    subset = extract(
        bundle(technique("T1595", "Active Scanning"), technique("T1595.001", "Scanning IP Blocks")),
        {"T1595", "T1595.001"},
    )

    assert (subset["attack_version"], subset["notice"]) == ("19.2", NOTICE)
    first, sub = subset["techniques"]
    assert (first["id"], first["name"], first["tactics"]) == (
        "T1595",
        "Active Scanning",
        ["reconnaissance"],
    )
    assert first["description"] == "Adversaries may scan. More text."
    assert (first["is_subtechnique"], first["parent_id"]) == (False, None)
    assert (sub["is_subtechnique"], sub["parent_id"]) == (True, "T1595")
    assert sub["url"] == "https://attack.mitre.org/techniques/T1595/001"


def test_another_attack_version_is_refused() -> None:
    with pytest.raises(SubsetError, match="expected ATT&CK 19.2"):
        extract(bundle(technique("T1595", "Active Scanning"), version="18.1"), {"T1595"})


def test_a_missing_or_revoked_technique_is_refused() -> None:
    revoked = technique("T1046", "Network Service Discovery", revoked=True)

    with pytest.raises(SubsetError, match="no current technique T1046"):
        extract(bundle(technique("T1595", "Active Scanning"), revoked), {"T1595", "T1046"})
