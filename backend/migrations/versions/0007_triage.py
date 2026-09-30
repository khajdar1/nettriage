"""Triage (spec §5.2, §5.4, §7): members change a finding's status and assignee, and comment on
it; every change is a row in the finding's history.

- `app_api` may update a finding's status, assignee and version, and nothing else about it. It
  may add events to a finding's history, as itself, but never change or remove one.
- An assignee is a member of the finding's org: a composite foreign key to `memberships`. When a
  membership ends, the API unassigns that member's findings and records it (Plan 4c); the
  foreign key's `SET NULL (assignee_id)` is the backstop, so a membership can always go.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE findings ADD CONSTRAINT findings_assignee_is_a_member
            FOREIGN KEY (org_id, assignee_id) REFERENCES memberships (org_id, user_id)
            ON DELETE SET NULL (assignee_id);
        CREATE INDEX findings_org_assignee ON findings (org_id, assignee_id)
            WHERE assignee_id IS NOT NULL;

        GRANT UPDATE (status, assignee_id, version) ON findings TO app_api;
        GRANT INSERT (id, org_id, finding_id, actor_id, type, payload)
            ON finding_events TO app_api;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE INSERT ON finding_events FROM app_api;
        REVOKE UPDATE ON findings FROM app_api;
        DROP INDEX findings_org_assignee;
        ALTER TABLE findings DROP CONSTRAINT findings_assignee_is_a_member;
        """
    )
