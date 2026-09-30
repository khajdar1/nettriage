"""Reference data in Postgres (spec §5.2): the detectors, synced from code, and the ATT&CK
techniques they can name, from the committed subset (nettriage.reference).

The deploy runs this as the schema owner right after migrating (tools/deploy/database.py), and
the test database runs it too. Every row is an upsert, so running it again changes only what
changed in code.

    NETTRIAGE_MIGRATION_DATABASE_URL=<owner url> python -m nettriage.adapters.reference_data
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import Engine, text

from nettriage.adapters.postgres import create_database_engine
from nettriage.domain.detection.engine import DETECTORS
from nettriage.reference import attack_reference

URL_ENV = "NETTRIAGE_MIGRATION_DATABASE_URL"

_UPSERT_TECHNIQUE = text(
    "INSERT INTO attack_techniques (id, stix_id, name, tactics, description, url, "
    "attack_version, is_subtechnique, parent_id, deprecated) VALUES (:id, :stix_id, :name, "
    ":tactics, :description, :url, :version, :sub, :parent, :deprecated) "
    "ON CONFLICT (id) DO UPDATE SET stix_id = EXCLUDED.stix_id, name = EXCLUDED.name, "
    "tactics = EXCLUDED.tactics, description = EXCLUDED.description, url = EXCLUDED.url, "
    "attack_version = EXCLUDED.attack_version, is_subtechnique = EXCLUDED.is_subtechnique, "
    "parent_id = EXCLUDED.parent_id, deprecated = EXCLUDED.deprecated"
)
_UPSERT_DETECTOR = text(
    "INSERT INTO detectors (id, name, description, version, candidate_techniques) "
    "VALUES (:id, :name, :description, :version, :techniques) "
    "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, "
    "version = EXCLUDED.version, candidate_techniques = EXCLUDED.candidate_techniques"
)


def sync_reference_data(engine: Engine) -> tuple[int, int]:
    """Upsert the detectors and techniques in one transaction. Returns their counts."""
    reference = attack_reference()
    parents_first = sorted(reference.techniques, key=lambda technique: technique.is_subtechnique)
    with engine.begin() as connection:
        for technique in parents_first:
            connection.execute(
                _UPSERT_TECHNIQUE,
                {
                    "id": technique.id,
                    "stix_id": technique.stix_id,
                    "name": technique.name,
                    "tactics": list(technique.tactics),
                    "description": technique.description,
                    "url": technique.url,
                    "version": reference.version,
                    "sub": technique.is_subtechnique,
                    "parent": technique.parent_id,
                    "deprecated": technique.deprecated,
                },
            )
        for detector in DETECTORS:
            connection.execute(
                _UPSERT_DETECTOR,
                {
                    "id": detector.id,
                    "name": detector.name,
                    "description": detector.description,
                    "version": detector.version,
                    "techniques": list(detector.candidate_techniques),
                },
            )
    return len(DETECTORS), len(reference.techniques)


def main() -> int:
    url = os.environ.get(URL_ENV)
    if not url:
        print(f"{URL_ENV} isn't set.", file=sys.stderr)
        return 2
    engine = create_database_engine(url, pool_size=1)
    try:
        detectors, techniques = sync_reference_data(engine)
    finally:
        engine.dispose()
    print(f"Reference data synced: {detectors} detectors, {techniques} ATT&CK techniques.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
