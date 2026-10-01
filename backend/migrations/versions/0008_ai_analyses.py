"""AI analyses (spec §5.2, §5.4, §8.3): one row per finding, model, prompt version and input, with
its outcome, the validated output and what it cost.

- `ai_analyses` is a tenant table with row-level security. A retry of the same input updates the
  same row (the unique key), so a finding never collects duplicate analyses for one model and
  prompt; a succeeded row is the cache.
- `app_triage` is the triage worker's role (Plan 5b runs it). It reads a finding, its evidence,
  techniques and detector, and the analyses; it writes analyses, adds the AI's techniques and an
  `ai_explained` event to the finding's history, and nothing else.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_analyses (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            status text NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed',
                                                   'skipped_budget', 'invalid_output')),
            provider text NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_-]{0,29}$'),
            model_id text NOT NULL CHECK (length(model_id) BETWEEN 1 AND 200),
            prompt_version text NOT NULL CHECK (prompt_version ~ '^v[0-9]+$'),
            output_schema_version text NOT NULL CHECK (output_schema_version ~ '^v[0-9]+$'),
            input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
            output jsonb,
            input_tokens integer CHECK (input_tokens >= 0),
            output_tokens integer CHECK (output_tokens >= 0),
            cost_usd numeric(10, 6) CHECK (cost_usd >= 0),
            latency_ms integer CHECK (latency_ms >= 0),
            error_code text CHECK (error_code ~ '^[a-z][a-z_]{0,49}$'),
            feedback text CHECK (feedback IN ('up', 'down')),
            feedback_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (finding_id, model_id, prompt_version, input_hash),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE,
            CHECK ((status = 'succeeded') = (output IS NOT NULL))
        );
        CREATE INDEX ai_analyses_org_created ON ai_analyses (org_id, created_at);
        CREATE INDEX ai_analyses_finding ON ai_analyses (finding_id, created_at DESC);
        CREATE TRIGGER ai_analyses_updated_at BEFORE UPDATE ON ai_analyses
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        ALTER TABLE ai_analyses ENABLE ROW LEVEL SECURITY;
        ALTER TABLE ai_analyses FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON ai_analyses
            USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id());

        -- Created without a login; the deploy gives it one once the worker exists (Plan 5b).
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_triage') THEN
                CREATE ROLE app_triage NOLOGIN;
            END IF;
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = 'app_triage'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION 'app_triage must not be a superuser or have BYPASSRLS, '
                    'CREATEROLE, CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = 'app_triage'
            ) THEN
                RAISE EXCEPTION 'app_triage must not be a member of another role';
            END IF;
        END $$;

        GRANT SELECT ON ai_analyses TO app_api;

        GRANT USAGE ON SCHEMA public TO app_triage;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_triage;
        GRANT SELECT ON findings, finding_evidence, finding_techniques, detectors,
            attack_techniques, ai_analyses TO app_triage;
        GRANT INSERT (id, org_id, finding_id, status, provider, model_id, prompt_version,
                      output_schema_version, input_hash, output, input_tokens, output_tokens,
                      cost_usd, latency_ms, error_code)
            ON ai_analyses TO app_triage;
        GRANT UPDATE (status, output, input_tokens, output_tokens, cost_usd, latency_ms,
                      error_code)
            ON ai_analyses TO app_triage;
        GRANT INSERT ON finding_techniques TO app_triage;
        GRANT UPDATE (rationale) ON finding_techniques TO app_triage;
        GRANT INSERT (id, org_id, finding_id, type, payload) ON finding_events TO app_triage;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE ai_analyses;
        REVOKE ALL ON finding_events, finding_techniques, findings, finding_evidence, detectors,
            attack_techniques FROM app_triage;
        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_triage;
        REVOKE USAGE ON SCHEMA public FROM app_triage;
        """
    )
