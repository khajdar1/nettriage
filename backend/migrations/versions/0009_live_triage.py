"""Live AI triage (spec §5.4, §9.4; Plan 5b).

- `app_analyze` may read a finding's ID, org, upload and severity, so it can queue each upload's
  most severe findings for an AI explanation, again when SQS delivers a message twice.
- `app_triage` may append to the audit log in the finding's org: the worker records
  `budget.exhausted` when an org's budget or the global cap first refuses a call that day.
- An analysis names its provider as OpenTelemetry does (`gen_ai.provider.name`), so Bedrock is
  `aws.bedrock`: the name may now contain a dot.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_analyses DROP CONSTRAINT ai_analyses_provider_check,
            ADD CONSTRAINT ai_analyses_provider_check
                CHECK (provider ~ '^[a-z][a-z0-9_.-]{0,29}$');
        GRANT SELECT (id, org_id, upload_id, severity) ON findings TO app_analyze;
        GRANT INSERT (id, org_id, actor_user_id, actor_type, action, target_type, target_id,
                      outcome, ip, user_agent, request_id, trace_id, details)
            ON audit_log TO app_triage;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE ALL ON audit_log FROM app_triage;
        REVOKE SELECT (id, org_id, upload_id, severity) ON findings FROM app_analyze;
        ALTER TABLE ai_analyses DROP CONSTRAINT ai_analyses_provider_check,
            ADD CONSTRAINT ai_analyses_provider_check
                CHECK (provider ~ '^[a-z][a-z0-9_-]{0,29}$');
        """
    )
