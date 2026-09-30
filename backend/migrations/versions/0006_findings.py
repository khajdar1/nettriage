"""Findings (spec §5.2, §5.3, §5.4): what the analyze worker stores for an upload, and the
reference data it points to.

- `detectors` and `attack_techniques` are reference data, the same for every org; the deploy
  syncs them from code after migrating (nettriage.adapters.reference_data).
- `findings`, `finding_evidence`, `finding_techniques` and `finding_events` are tenant tables
  with row-level security. Composite foreign keys keep a finding in its upload's org, and its
  evidence, techniques and events in the finding's.
- `app_analyze` is the analyze worker's role: it reads uploads, moves them on (status and
  results only) and inserts findings. It can't read findings back or touch anything else.
- `app_api` reads findings; Plan 4c lets it triage them.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_TABLES = ("findings", "finding_evidence", "finding_techniques", "finding_events")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE detectors (
            id text PRIMARY KEY CHECK (id ~ '^[a-z][a-z_]*$'),
            name text NOT NULL CHECK (length(name) <= 100),
            description text NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            candidate_techniques text[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE attack_techniques (
            id text PRIMARY KEY CHECK (id ~ '^T[0-9]{4}(\\.[0-9]{3})?$'),
            stix_id text NOT NULL UNIQUE,
            name text NOT NULL,
            tactics text[] NOT NULL,
            description text NOT NULL,
            url text NOT NULL CHECK (url LIKE 'https://attack.mitre.org/techniques/%'),
            attack_version text NOT NULL,
            is_subtechnique boolean NOT NULL,
            parent_id text REFERENCES attack_techniques (id),
            deprecated boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (is_subtechnique = (parent_id IS NOT NULL))
        );

        CREATE TABLE findings (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            upload_id uuid NOT NULL,
            detector_id text NOT NULL REFERENCES detectors (id),
            detector_version integer NOT NULL CHECK (detector_version > 0),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
            status text NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'investigating', 'resolved', 'false_positive')),
            title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            src_ip inet NOT NULL,
            dst_ip inet,
            dst_port integer CHECK (dst_port BETWEEN 0 AND 65535),
            protocol smallint CHECK (protocol BETWEEN 0 AND 255),
            time_window tstzrange NOT NULL,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            assignee_id uuid REFERENCES users (id),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, upload_id, fingerprint),
            UNIQUE (org_id, id),
            FOREIGN KEY (org_id, upload_id) REFERENCES uploads (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX findings_org_triage ON findings (org_id, status, severity, created_at DESC);
        CREATE INDEX findings_org_upload ON findings (org_id, upload_id);
        CREATE INDEX findings_org_source ON findings (org_id, src_ip);

        CREATE TABLE finding_evidence (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            src_ip inet NOT NULL,
            dst_ip inet NOT NULL,
            src_port integer NOT NULL CHECK (src_port BETWEEN 0 AND 65535),
            dst_port integer NOT NULL CHECK (dst_port BETWEEN 0 AND 65535),
            protocol smallint NOT NULL CHECK (protocol BETWEEN 0 AND 255),
            packets bigint NOT NULL CHECK (packets >= 0),
            bytes bigint NOT NULL CHECK (bytes >= 0),
            start_ts timestamptz NOT NULL,
            end_ts timestamptz NOT NULL,
            action text NOT NULL CHECK (action IN ('ACCEPT', 'REJECT')),
            line_no integer NOT NULL CHECK (line_no > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX finding_evidence_finding ON finding_evidence (finding_id, start_ts);

        CREATE TABLE finding_techniques (
            finding_id uuid NOT NULL,
            technique_id text NOT NULL REFERENCES attack_techniques (id),
            source text NOT NULL CHECK (source IN ('detector', 'ai')),
            org_id uuid NOT NULL,
            rationale text CHECK (length(rationale) <= 1000),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (finding_id, technique_id, source),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE
        );

        CREATE TABLE finding_events (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            actor_id uuid REFERENCES users (id),
            type text NOT NULL CHECK (type IN ('created', 'status_changed', 'assigned',
                                               'commented', 'ai_explained')),
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (org_id, finding_id) REFERENCES findings (org_id, id) ON DELETE CASCADE
        );
        CREATE INDEX finding_events_finding ON finding_events (finding_id, created_at);

        CREATE TRIGGER detectors_updated_at BEFORE UPDATE ON detectors
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER attack_techniques_updated_at BEFORE UPDATE ON attack_techniques
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER findings_updated_at BEFORE UPDATE ON findings
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant ON {table} "
            "USING (org_id = app_org_id()) WITH CHECK (org_id = app_org_id())"
        )
    op.execute(
        """
        -- Created without a login; the deploy gives it one (tools/deploy/database.py).
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_analyze') THEN
                CREATE ROLE app_analyze NOLOGIN;
            END IF;
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = 'app_analyze'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION 'app_analyze must not be a superuser or have BYPASSRLS, '
                    'CREATEROLE, CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = 'app_analyze'
            ) THEN
                RAISE EXCEPTION 'app_analyze must not be a member of another role';
            END IF;
        END $$;

        GRANT SELECT ON detectors, attack_techniques TO app_api, app_analyze;
        GRANT SELECT ON findings, finding_evidence, finding_techniques, finding_events TO app_api;

        GRANT USAGE ON SCHEMA public TO app_analyze;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_analyze;
        GRANT SELECT ON uploads TO app_analyze;
        GRANT UPDATE (status, failure_reason, rows_parsed, rows_rejected, rejected_samples,
                      findings_truncated, flow_time_range, processed_at)
            ON uploads TO app_analyze;
        GRANT INSERT (id, org_id, upload_id, detector_id, detector_version, fingerprint, severity,
                      title, src_ip, dst_ip, dst_port, protocol, time_window, metrics)
            ON findings TO app_analyze;
        GRANT INSERT ON finding_evidence, finding_techniques TO app_analyze;
        GRANT INSERT (id, org_id, finding_id, type, payload) ON finding_events TO app_analyze;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE finding_events, finding_techniques, finding_evidence, findings,
            attack_techniques, detectors;
        REVOKE ALL ON uploads FROM app_analyze;
        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_analyze;
        REVOKE USAGE ON SCHEMA public FROM app_analyze;
        """
    )
