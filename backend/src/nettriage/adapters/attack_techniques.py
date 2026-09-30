"""ATT&CK techniques from the reference table (spec §5.2, §7), the same for every org."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import tenant_transaction


@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    tactics: tuple[str, ...]
    description: str
    url: str
    is_subtechnique: bool
    parent_id: str | None
    deprecated: bool
    attack_version: str


def get_technique(engine: Engine, user_id: UUID, technique_id: str) -> Technique | None:
    with tenant_transaction(engine, user_id=user_id) as connection:
        row = connection.execute(
            text(
                "SELECT id, name, tactics, description, url, is_subtechnique, parent_id, "
                "deprecated, attack_version FROM attack_techniques WHERE id = :id"
            ),
            {"id": technique_id},
        ).one_or_none()
    if row is None:
        return None
    return Technique(
        id=row.id,
        name=row.name,
        tactics=tuple(row.tactics),
        description=row.description,
        url=row.url,
        is_subtechnique=row.is_subtechnique,
        parent_id=row.parent_id,
        deprecated=row.deprecated,
        attack_version=row.attack_version,
    )
