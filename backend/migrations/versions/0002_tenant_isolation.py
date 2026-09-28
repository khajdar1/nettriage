"""Tenant isolation: row-level security, the `app_api` role and its grants (spec §5.3, §5.4).

Every tenant table has ENABLE and FORCE ROW LEVEL SECURITY. The policies compare against
`app.org_id` and `app.user_id`, which `tenant_transaction` sets per transaction; an unset or
empty setting reads as NULL and matches no rows.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = ("organizations", "memberships", "invitations", "audit_log")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app_org_id() RETURNS uuid LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$;
        CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        -- An organization is visible when it's the transaction's org, or the user is a member
        -- (for "my organizations"). Writes are only allowed to the transaction's org.
        CREATE POLICY tenant ON organizations
            USING (id = app_org_id()
                   OR id IN (SELECT org_id FROM memberships WHERE user_id = app_user_id()))
            WITH CHECK (id = app_org_id());

        -- A user also sees their own memberships in every org.
        CREATE POLICY tenant ON memberships
            USING (org_id = app_org_id() OR user_id = app_user_id())
            WITH CHECK (org_id = app_org_id());

        CREATE POLICY tenant ON invitations
            USING (org_id = app_org_id())
            WITH CHECK (org_id = app_org_id());

        -- Events outside any org (sign-in, denials on non-org routes) have a NULL org_id.
        CREATE POLICY tenant_read ON audit_log FOR SELECT
            USING (org_id = app_org_id());
        CREATE POLICY tenant_append ON audit_log FOR INSERT
            WITH CHECK (org_id IS NULL OR org_id = app_org_id());

        -- On Neon, Terraform creates app_api with a login and password first. Locally and in
        -- CI it's created here, without a login.
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_api') THEN
                CREATE ROLE app_api NOLOGIN;
            END IF;
        END $$;

        GRANT USAGE ON SCHEMA public TO app_api;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_api;
        GRANT SELECT, INSERT ON users TO app_api;
        GRANT UPDATE (email, display_name, last_login_at) ON users TO app_api;
        GRANT SELECT, INSERT, DELETE ON organizations TO app_api;
        GRANT UPDATE (name) ON organizations TO app_api;
        GRANT SELECT, INSERT, DELETE ON memberships TO app_api;
        GRANT UPDATE (role) ON memberships TO app_api;
        GRANT SELECT, INSERT, DELETE ON invitations TO app_api;
        GRANT UPDATE (accepted_at, accepted_by, revoked_at) ON invitations TO app_api;
        GRANT SELECT, INSERT ON audit_log TO app_api;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE ALL ON users, organizations, memberships, invitations, audit_log FROM app_api;
        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_api;
        REVOKE USAGE ON SCHEMA public FROM app_api;
        DROP POLICY tenant_append ON audit_log;
        DROP POLICY tenant_read ON audit_log;
        DROP POLICY tenant ON invitations;
        DROP POLICY tenant ON memberships;
        DROP POLICY tenant ON organizations;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_user_id(); DROP FUNCTION app_org_id();")
