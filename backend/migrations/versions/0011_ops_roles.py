"""The ops job's roles (Plan 7a §3): `app_ops` for the daily cleanup and the hourly check, and
`app_backup` for the nightly dump.

Every tenant table forces row-level security, even for its owner (Plan 3a), so neither role gets
`BYPASSRLS` nor works through `SECURITY DEFINER` functions (amending spec §5.4). Instead:
- `app_ops` has column grants and one row policy per cleanup rule, permissive and restrictive,
  so even a broad statement, or one in a transaction that names an organization,
  changes only the rows a rule targets: uploads waiting or analyzing for over 2 hours, which may
  only become expired or failed; invitations nobody accepted, past their date; audit rows over
  180 days old. The audit log's trigger lets `app_ops` delete; everyone else is still refused.
- `app_backup` may read every table, and every row-secured table has a read-all policy for it,
  so `pg_dump --enable-row-security` sees every row. A test fails if a later table misses either.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

STALE_UPLOAD = (
    "status IN ('pending_upload', 'processing') AND created_at < now() - interval '2 hours'"
)
# An UPDATE with a WHERE clause checks the new row against the read policy too, so reading
# covers the states the cleanup sets as well as those it changes.
READABLE_UPLOAD = (
    "status IN ('pending_upload', 'processing', 'expired', 'failed') "
    "AND created_at < now() - interval '2 hours'"
)
LAPSED_INVITATION = "accepted_at IS NULL AND expires_at < now()"
OLD_AUDIT_ROW = "created_at < now() - interval '180 days'"


def create_role(role: str) -> str:
    """Created without a login (the deploy gives it one), with the same guards as the others."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN;
            END IF;
            IF EXISTS (
                SELECT FROM pg_roles WHERE rolname = '{role}'
                AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
            ) THEN
                RAISE EXCEPTION '{role} must not be a superuser or have BYPASSRLS, '
                    'CREATEROLE, CREATEDB or REPLICATION';
            END IF;
            IF EXISTS (
                SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = '{role}'
            ) THEN
                RAISE EXCEPTION '{role} must not be a member of another role';
            END IF;
        END $$;
    """  # noqa: S608 - fixed role names, not input


def upgrade() -> None:
    op.execute(create_role("app_ops") + create_role("app_backup"))
    op.execute(
        f"""
        GRANT USAGE ON SCHEMA public TO app_ops, app_backup;
        GRANT EXECUTE ON FUNCTION app_org_id(), app_user_id() TO app_ops, app_backup;

        GRANT SELECT (id, status, created_at), UPDATE (status, failure_reason, processed_at)
            ON uploads TO app_ops;
        CREATE POLICY ops_read ON uploads FOR SELECT TO app_ops USING ({READABLE_UPLOAD});
        CREATE POLICY ops_update ON uploads FOR UPDATE TO app_ops
            USING ({STALE_UPLOAD}) WITH CHECK (status IN ('expired', 'failed'));

        GRANT SELECT (id, accepted_at, expires_at), DELETE ON invitations TO app_ops;
        CREATE POLICY ops_read ON invitations FOR SELECT TO app_ops USING ({LAPSED_INVITATION});
        CREATE POLICY ops_delete ON invitations FOR DELETE TO app_ops
            USING ({LAPSED_INVITATION});

        GRANT SELECT (id, created_at), DELETE ON audit_log TO app_ops;
        CREATE POLICY ops_read ON audit_log FOR SELECT TO app_ops USING ({OLD_AUDIT_ROW});
        CREATE POLICY ops_delete ON audit_log FOR DELETE TO app_ops USING ({OLD_AUDIT_ROW});

        -- The tenant policies apply to every role once a transaction names its organization,
        -- and permissive policies add up. So each rule is also restrictive: it limits app_ops
        -- whatever another policy allows.
        CREATE POLICY ops_only_read ON uploads AS RESTRICTIVE FOR SELECT TO app_ops
            USING ({READABLE_UPLOAD});
        CREATE POLICY ops_only_update ON uploads AS RESTRICTIVE FOR UPDATE TO app_ops
            USING ({STALE_UPLOAD}) WITH CHECK (status IN ('expired', 'failed'));
        CREATE POLICY ops_only_read ON invitations AS RESTRICTIVE FOR SELECT TO app_ops
            USING ({LAPSED_INVITATION});
        CREATE POLICY ops_only_delete ON invitations AS RESTRICTIVE FOR DELETE TO app_ops
            USING ({LAPSED_INVITATION});
        CREATE POLICY ops_only_read ON audit_log AS RESTRICTIVE FOR SELECT TO app_ops
            USING ({OLD_AUDIT_ROW});
        CREATE POLICY ops_only_delete ON audit_log AS RESTRICTIVE FOR DELETE TO app_ops
            USING ({OLD_AUDIT_ROW});

        -- Still append-only for everyone but the cleanup, whose policy limits it to old rows.
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_user = 'app_ops' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;

        GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_backup;
        GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO app_backup;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_backup;
        DO $$
        DECLARE
            secured text;
        BEGIN
            FOR secured IN
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
            LOOP
                EXECUTE format(
                    'CREATE POLICY backup_read ON %I FOR SELECT TO app_backup USING (true)',
                    secured
                );
            END LOOP;
        END $$;
        """  # noqa: S608 - fixed policy predicates, not input
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            secured text;
        BEGIN
            FOR secured IN
                SELECT tablename FROM pg_policies
                WHERE schemaname = 'public' AND policyname = 'backup_read'
            LOOP
                EXECUTE format('DROP POLICY backup_read ON %I', secured);
            END LOOP;
        END $$;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM app_backup;
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_backup;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM app_backup;

        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END $$;
        DROP POLICY ops_read ON uploads;
        DROP POLICY ops_update ON uploads;
        DROP POLICY ops_read ON invitations;
        DROP POLICY ops_delete ON invitations;
        DROP POLICY ops_read ON audit_log;
        DROP POLICY ops_delete ON audit_log;
        DROP POLICY ops_only_read ON uploads;
        DROP POLICY ops_only_update ON uploads;
        DROP POLICY ops_only_read ON invitations;
        DROP POLICY ops_only_delete ON invitations;
        DROP POLICY ops_only_read ON audit_log;
        DROP POLICY ops_only_delete ON audit_log;
        REVOKE ALL ON uploads, invitations, audit_log FROM app_ops;

        REVOKE ALL ON FUNCTION app_org_id(), app_user_id() FROM app_ops, app_backup;
        REVOKE USAGE ON SCHEMA public FROM app_ops, app_backup;
        """
    )
