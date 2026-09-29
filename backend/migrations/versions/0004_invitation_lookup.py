"""Accepting an invitation (Plan 3c, spec §6.3): the invitee isn't a member of the org yet, so
row-level security hides the invitation from them. A transaction that sets
`app.invitation_token_hash` may read the one invitation with that hash.

A SECURITY DEFINER function can't do this lookup instead: `invitations` has FORCE ROW LEVEL
SECURITY, which applies to its owner too, and on Neon the owner isn't a superuser. Knowing the
hash already means knowing the invitation (the link holds the token), so the policy grants
nothing more than the link does.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app_invitation_token_hash() RETURNS text LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('app.invitation_token_hash', true), '') $$;
        GRANT EXECUTE ON FUNCTION app_invitation_token_hash() TO app_api;

        CREATE POLICY by_token ON invitations FOR SELECT
            USING (token_hash = app_invitation_token_hash());
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY by_token ON invitations;
        DROP FUNCTION app_invitation_token_hash();
        """
    )
