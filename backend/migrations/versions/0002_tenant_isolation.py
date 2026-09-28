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
        -- Inside an org's transaction only that org is visible, so a query that forgets its
        -- org filter still can't count or change another tenant's rows. With no org set, a
        -- user sees the orgs they belong to and their own memberships ("my organizations").
        -- Writes are per command so UPDATE and DELETE never use the wider read rule.
        CREATE POLICY tenant_read ON organizations FOR SELECT
            USING (id = app_org_id()
                   OR (app_org_id() IS NULL
                       AND id IN (SELECT org_id FROM memberships
                                  WHERE user_id = app_user_id())));
        CREATE POLICY tenant_insert ON organizations FOR INSERT
            WITH CHECK (id = app_org_id());
        CREATE POLICY tenant_update ON organizations FOR UPDATE
            USING (id = app_org_id())
            WITH CHECK (id = app_org_id());
        CREATE POLICY tenant_delete ON organizations FOR DELETE
            USING (id = app_org_id());

        CREATE POLICY tenant_read ON memberships FOR SELECT
            USING (org_id = app_org_id()
                   OR (app_org_id() IS NULL AND user_id = app_user_id()));
        CREATE POLICY tenant_insert ON memberships FOR INSERT
            WITH CHECK (org_id = app_org_id());
        CREATE POLICY tenant_update ON memberships FOR UPDATE
            USING (org_id = app_org_id())
            WITH CHECK (org_id = app_org_id());
        CREATE POLICY tenant_delete ON memberships FOR DELETE
            USING (org_id = app_org_id());

        CREATE POLICY tenant ON invitations
            USING (org_id = app_org_id())
            WITH CHECK (org_id = app_org_id());

        -- Events outside any org (sign-in, denials on non-org routes) have a NULL org_id.
        CREATE POLICY tenant_read ON audit_log FOR SELECT
            USING (org_id = app_org_id());
        CREATE POLICY tenant_append ON audit_log FOR INSERT
            WITH CHECK (org_id IS NULL OR org_id = app_org_id());

        -- Created without a login. On Neon the deploy then gives it a login and a password
        -- (tools/deploy/database.py); tests do the same with a test-only password.
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
        DROP POLICY tenant_delete ON memberships;
        DROP POLICY tenant_update ON memberships;
        DROP POLICY tenant_insert ON memberships;
        DROP POLICY tenant_read ON memberships;
        DROP POLICY tenant_delete ON organizations;
        DROP POLICY tenant_update ON organizations;
        DROP POLICY tenant_insert ON organizations;
        DROP POLICY tenant_read ON organizations;
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_user_id(); DROP FUNCTION app_org_id();")
