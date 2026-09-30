"""Write the ATT&CK techniques NetTriage's detectors can name (spec §5.2) from MITRE's STIX
bundle into backend/src/nettriage/reference/attack_techniques.json, with MITRE's notice.

Download the bundle once, then run the tool on it:

  curl -fLO https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack-19.2.json
  uv run --project backend python -m tools.attack_subset enterprise-attack-19.2.json

The output is committed; nothing downloads ATT&CK at build or deploy time.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ATTACK_VERSION = "19.2"
SOURCE = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    f"enterprise-attack/enterprise-attack-{ATTACK_VERSION}.json"
)
OUT = (
    Path(__file__).resolve().parents[1]
    / "backend" / "src" / "nettriage" / "reference" / "attack_techniques.json"
)
CITATION = re.compile(r"\(Citation: [^)]*\)")
# ATT&CK's terms of use (https://attack.mitre.org/resources/legal-and-branding/terms-of-use/):
# a copy is licensed only if it carries MITRE's copyright designation and this license.
LICENSE = (
    "The MITRE Corporation (MITRE) hereby grants you a non-exclusive, royalty-free license to "
    "use ATT&CK® for research, development, and commercial purposes. Any copy you make for "
    "such purposes is authorized provided that you reproduce MITRE's copyright designation and "
    "this license in any such copy. © 2026 The MITRE Corporation. This work is reproduced "
    "and distributed with the permission of The MITRE Corporation."
)


class SubsetError(Exception):
    """The bundle isn't the expected ATT&CK version, or lacks a technique a detector names."""


def wanted_ids() -> set[str]:
    """Every technique a detector can name, and the parent of each sub-technique."""
    from nettriage.domain.detection.engine import DETECTORS

    ids = {technique for detector in DETECTORS for technique in detector.candidate_techniques}
    return ids | {technique.split(".")[0] for technique in ids}


def extract(bundle: dict[str, Any], wanted: set[str], version: str = ATTACK_VERSION) -> dict[str, Any]:
    objects = bundle["objects"]
    versions = [o.get("x_mitre_version") for o in objects if o["type"] == "x-mitre-collection"]
    if versions != [version]:
        raise SubsetError(f"expected ATT&CK {version}, the bundle is {versions or 'unversioned'}")
    notices = [
        o["definition"]["statement"]
        for o in objects
        if o["type"] == "marking-definition" and o.get("definition_type") == "statement"
    ]
    if len(notices) != 1:
        raise SubsetError(f"expected one copyright statement, found {len(notices)}")
    found: dict[str, dict[str, Any]] = {}
    for obj in objects:
        if obj["type"] != "attack-pattern" or obj.get("revoked"):
            continue
        reference = next(
            (r for r in obj.get("external_references", []) if r.get("source_name") == "mitre-attack"),
            None,
        )
        if reference is not None and reference["external_id"] in wanted:
            found[reference["external_id"]] = _technique(obj, reference)
    missing = sorted(wanted - found.keys())
    if missing:
        raise SubsetError(f"ATT&CK {version} has no current technique {', '.join(missing)}")
    return {
        "attack_version": version,
        "source": SOURCE,
        "notice": notices[0],
        "license": LICENSE,
        "techniques": [found[technique] for technique in sorted(found)],
    }


def _technique(obj: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    technique_id: str = reference["external_id"]
    subtechnique = bool(obj.get("x_mitre_is_subtechnique"))
    return {
        "id": technique_id,
        "stix_id": obj["id"],
        "name": obj["name"],
        "tactics": [
            phase["phase_name"]
            for phase in obj.get("kill_chain_phases", [])
            if phase.get("kill_chain_name") == "mitre-attack"
        ],
        "description": _clean(obj.get("description", "")),
        "url": reference["url"],
        "is_subtechnique": subtechnique,
        "parent_id": technique_id.split(".")[0] if subtechnique else None,
        "deprecated": bool(obj.get("x_mitre_deprecated")),
    }


def _clean(description: str) -> str:
    """Without MITRE's citation markers, which point into a reference list we don't keep."""
    return re.sub(r"[ \t]+", " ", CITATION.sub("", description)).strip()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    bundle = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    try:
        subset = extract(bundle, wanted_ids())
    except SubsetError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    OUT.write_text(
        json.dumps(subset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Wrote {len(subset['techniques'])} ATT&CK {subset['attack_version']} techniques to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
