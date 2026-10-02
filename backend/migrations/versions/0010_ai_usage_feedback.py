"""AI usage, feedback and the AI's techniques (spec §5.2, §5.4, §7; Plan 5c).

- `ai_usage` counts every model call per org and UTC day: calls, tokens and cost, including
  retries and repairs that store no analysis (the owner's decision). The triage worker adds to
  it; the API reads it for `GET …/usage`.
- `app_api` may rate an analysis (`feedback`, `feedback_by`) and nothing else on it. A rating
  doesn't make the analysis newer: `updated_at` moves only when an attempt changes it.
- `app_triage` may delete the AI's own techniques of a finding, never the detector's, so the
  latest succeeded analysis replaces them (the owner's decision).

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_usage (
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            day date NOT NULL,
            calls integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
            input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            cost_usd numeric(12, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
            PRIMARY KEY (org_id, day)
        );
        ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ai_usage FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON ai_usage
            USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());
        GRANT SELECT ON ai_usage TO app_api;
        GRANT SELECT, INSERT (org_id, day, calls, input_tokens, output_tokens, cost_usd),
            UPDATE (calls, input_tokens, output_tokens, cost_usd)
            ON ai_usage TO app_triage;

        GRANT UPDATE (feedback, feedback_by) ON ai_analyses TO app_api;
        DROP TRIGGER ai_analyses_updated_at ON ai_analyses;
        CREATE TRIGGER ai_analyses_updated_at
            BEFORE UPDATE OF status, output, input_tokens, output_tokens, cost_usd, latency_ms,
                error_code
            ON ai_analyses FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        GRANT DELETE ON finding_techniques TO app_triage;
        CREATE POLICY ai_rows_only ON finding_techniques AS RESTRICTIVE
            FOR DELETE TO app_triage USING (source = 'ai');
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY ai_rows_only ON finding_techniques;
        REVOKE DELETE ON finding_techniques FROM app_triage;
        DROP TRIGGER ai_analyses_updated_at ON ai_analyses;
        CREATE TRIGGER ai_analyses_updated_at BEFORE UPDATE ON ai_analyses
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        REVOKE UPDATE (feedback, feedback_by) ON ai_analyses FROM app_api;
        DROP TABLE ai_usage;
        """
    )
