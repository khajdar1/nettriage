"""Sign-in and hardening (Plan 3b): row-level security on `users`, the sign-in function, and the
Plan 3a review's hardening items.

- `users` gets row-level security, ENABLE without FORCE. `app_api` sees its own row, and inside
  an org's transaction the members of that org (when it is one of them). The table's owner
  bypasses it, so `sign_in_user`, a SECURITY DEFINER function owned by the owner, can find or
  create a user before anyone knows who the user is.
- `app_api` loses INSERT on `users` (sign-in goes through the function) and keeps UPDATE of
  `display_name`, on its own row only.
- INSERT grants become column-level, so `app_api` can't set `is_demo`, `accepted_at`,
  `created_at` and the like.
- `audit_log` also rejects TRUNCATE.
- PUBLIC loses TEMPORARY on this database, so `app_api` can't create a temporary table that
  shadows a real one.
- The migration stops if `app_api` already exists with rights it must not have.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = 'app_api'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION 'app_api must not be a superuser or have BYPASSRLS, CREATEROLE, '
                    'CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = 'app_api'
            ) THEN
                RAISE EXCEPTION 'app_api must not be a member of another role';
            END IF;
        END $$;

        ALTER TABLE users ENABLE ROW LEVEL SECURITY;
        CREATE POLICY self_or_fellow_member ON users FOR SELECT
            USING (id = app_user_id()
                   OR (id IN (SELECT user_id FROM memberships WHERE org_id = app_org_id())
                       AND EXISTS (SELECT FROM memberships
                                   WHERE org_id = app_org_id() AND user_id = app_user_id())));
        CREATE POLICY self_update ON users FOR UPDATE
            USING (id = app_user_id())
            WITH CHECK (id = app_user_id());

        -- Sign-in: find the user by Cognito's `sub`, or create them. Returns whether the row is
        -- new and whether the account is disabled; a disabled account's last_login_at stays.
        CREATE FUNCTION sign_in_user(p_new_id uuid, p_sub text, p_email text)
            RETURNS TABLE (user_id uuid, created boolean, disabled boolean)
            LANGUAGE sql SECURITY DEFINER
            SET search_path = pg_catalog, public, pg_temp
        AS $$
            INSERT INTO users AS u (id, cognito_sub, email, last_login_at)
            VALUES (p_new_id, p_sub, p_email, now())
            ON CONFLICT (cognito_sub) DO UPDATE
                SET email = excluded.email,
                    last_login_at = CASE WHEN u.disabled_at IS NULL THEN now()
                                         ELSE u.last_login_at END
            RETURNING u.id, u.id = p_new_id, u.disabled_at IS NOT NULL
        $$;
        REVOKE ALL ON FUNCTION sign_in_user(uuid, text, text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION sign_in_user(uuid, text, text) TO app_api;

        REVOKE INSERT, UPDATE ON users FROM app_api;
        GRANT UPDATE (display_name) ON users TO app_api;
        REVOKE INSERT ON organizations, memberships, invitations, audit_log FROM app_api;
        GRANT INSERT (id, name, slug, created_by) ON organizations TO app_api;
        GRANT INSERT (org_id, user_id, role, invited_by) ON memberships TO app_api;
        GRANT INSERT (id, org_id, email, role, token_hash, expires_at, created_by)
            ON invitations TO app_api;
        GRANT INSERT (id, org_id, actor_user_id, actor_type, action, target_type, target_id,
                      outcome, ip, user_agent, request_id, trace_id, details)
            ON audit_log TO app_api;

        CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();

        DO $$
        BEGIN
            EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
        END $$;
        DROP TRIGGER audit_log_no_truncate ON audit_log;

        REVOKE INSERT ON organizations, memberships, invitations, audit_log FROM app_api;
        GRANT INSERT ON organizations, memberships, invitations, audit_log TO app_api;
        REVOKE UPDATE ON users FROM app_api;
        GRANT INSERT ON users TO app_api;
        GRANT UPDATE (email, display_name, last_login_at) ON users TO app_api;

        DROP FUNCTION sign_in_user(uuid, text, text);
        DROP POLICY self_update ON users;
        DROP POLICY self_or_fellow_member ON users;
        ALTER TABLE users DISABLE ROW LEVEL SECURITY;
        """
    )
