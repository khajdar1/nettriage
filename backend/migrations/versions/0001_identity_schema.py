"""Identity and organizations: users, organizations, memberships, invitations, audit log
(spec §5.2).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ROLES = "('owner', 'admin', 'analyst', 'viewer')"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END $$;

        CREATE TABLE users (
            id uuid PRIMARY KEY,
            cognito_sub text NOT NULL UNIQUE,
            email text NOT NULL CHECK (length(email) <= 320),
            display_name text CHECK (length(display_name) <= 100),
            last_login_at timestamptz,
            disabled_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE organizations (
            id uuid PRIMARY KEY,
            name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            slug text NOT NULL UNIQUE
                CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 60),
            is_demo boolean NOT NULL DEFAULT false,
            created_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
        + f"""
        CREATE TABLE memberships (
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN {ROLES}),
            invited_by uuid REFERENCES users (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, user_id)
        );

        CREATE TABLE invitations (
            id uuid PRIMARY KEY,
            org_id uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            email text NOT NULL CHECK (length(email) <= 320),
            role text NOT NULL CHECK (role IN {ROLES}),
            token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
            expires_at timestamptz NOT NULL,
            created_by uuid NOT NULL REFERENCES users (id),
            accepted_at timestamptz,
            accepted_by uuid REFERENCES users (id),
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (org_id, id)
        );
        """
        + """
        -- At most one pending invitation per email in an org. "Pending" can't include
        -- "not expired" (an index can't call now()); expired rows are revoked before a new
        -- invitation for the same email is created.
        CREATE UNIQUE INDEX invitations_one_pending_per_email
            ON invitations (org_id, lower(email))
            WHERE accepted_at IS NULL AND revoked_at IS NULL;

        CREATE TABLE audit_log (
            id uuid PRIMARY KEY,
            org_id uuid,
            actor_user_id uuid,
            actor_type text NOT NULL CHECK (actor_type IN ('user', 'system', 'anonymous')),
            action text NOT NULL CHECK (length(action) <= 100),
            target_type text CHECK (length(target_type) <= 50),
            target_id text CHECK (length(target_id) <= 100),
            outcome text NOT NULL CHECK (outcome IN ('success', 'denied', 'error')),
            ip inet,
            user_agent text CHECK (length(user_agent) <= 256),
            request_id text CHECK (length(request_id) <= 100),
            trace_id text CHECK (length(trace_id) <= 64),
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX audit_log_org_created ON audit_log (org_id, created_at DESC);

        CREATE TRIGGER users_updated_at BEFORE UPDATE ON users
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER organizations_updated_at BEFORE UPDATE ON organizations
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER memberships_updated_at BEFORE UPDATE ON memberships
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER invitations_updated_at BEFORE UPDATE ON invitations
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        -- The audit log is append-only. Plan 7's retention function amends this trigger.
        CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;
        CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit_log;
        DROP FUNCTION audit_log_append_only();
        DROP TABLE invitations;
        DROP TABLE memberships;
        DROP TABLE organizations;
        DROP TABLE users;
        DROP FUNCTION set_updated_at();
        """
    )
