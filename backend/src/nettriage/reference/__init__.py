"""Reference data that ships with the code (spec §5.2): the ATT&CK techniques the detectors can
name, taken from MITRE's ATT&CK STIX bundle by tools/attack_subset.py, with MITRE's notice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files


@dataclass(frozen=True)
class AttackTechnique:
    id: str
    stix_id: str
    name: str
    tactics: tuple[str, ...]
    description: str
    url: str
    is_subtechnique: bool
    parent_id: str | None
    deprecated: bool


@dataclass(frozen=True)
class AttackReference:
    version: str
    notice: str
    techniques: tuple[AttackTechnique, ...]


@cache
def attack_reference() -> AttackReference:
    raw = json.loads(files(__name__).joinpath("attack_techniques.json").read_text(encoding="utf-8"))
    return AttackReference(
        version=raw["attack_version"],
        notice=raw["notice"],
        techniques=tuple(
            AttackTechnique(
                id=item["id"],
                stix_id=item["stix_id"],
                name=item["name"],
                tactics=tuple(item["tactics"]),
                description=item["description"],
                url=item["url"],
                is_subtechnique=item["is_subtechnique"],
                parent_id=item["parent_id"],
                deprecated=item["deprecated"],
            )
            for item in raw["techniques"]
        ),
    )
