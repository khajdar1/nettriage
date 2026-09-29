"""Uploads (spec §5.2, §5.3, §5.4): one row per file a member uploads. The API creates it as
`pending_upload` with a presigned PUT; the analyze worker (Plan 4b) moves it on.

`app_api` may read uploads and insert new ones, but only the columns a new upload has: it can't
set a status, statistics or results, and it can't update or delete a row. Deleting the org
removes its uploads through the cascade.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

STATUSES = "('pending_upload', 'processing', 'analyzed', 'failed', 'expired')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE uploads (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            uploaded_by uuid NOT NULL REFERENCES users (id),
            original_filename text NOT NULL
                CHECK (length(original_filename) BETWEEN 1 AND 255),
            s3_key text NOT NULL UNIQUE CHECK (length(s3_key) <= 200),
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            format text NOT NULL DEFAULT 'aws_vpc_flow_logs'
                CHECK (format IN ('aws_vpc_flow_logs')),
            status text NOT NULL DEFAULT 'pending_upload' CHECK (status IN {STATUSES}),
            failure_reason text CHECK (length(failure_reason) <= 500),
            rows_parsed integer CHECK (rows_parsed >= 0),
            rows_rejected integer CHECK (rows_rejected >= 0),
            rejected_samples jsonb NOT NULL DEFAULT '[]'::jsonb,
            findings_truncated integer NOT NULL DEFAULT 0 CHECK (findings_truncated >= 0),
            flow_time_range tstzrange,
            processed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id)
        );
        CREATE INDEX uploads_org_created ON uploads (org_id, created_at DESC);
        CREATE TRIGGER uploads_updated_at BEFORE UPDATE ON uploads
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        ALTER TABLE uploads ENABLE ROW LEVEL SECURITY;
        ALTER TABLE uploads FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant ON uploads
            USING (org_id = app_org_id())
            WITH CHECK (org_id = app_org_id());

        GRANT SELECT ON uploads TO app_api;
        GRANT INSERT (id, org_id, uploaded_by, original_filename, s3_key, size_bytes, sha256)
            ON uploads TO app_api;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE uploads")
