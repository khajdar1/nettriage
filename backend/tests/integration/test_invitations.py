"""Invitations as the API's role (spec §5.7, §6.3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import Database
from sqlalchemy import text
from tenantdata import add_invitation, add_member, add_user

from nettriage.adapters.invitations import (
    accept_invitation,
    create_invitation,
    list_invitations,
    revoke_invitation,
)
from nettriage.adapters.organizations import create_org, get_org, role_of
from nettriage.adapters.postgres import tenant_transaction
from nettriage.application.organizations import (
    Conflict,
    Forbidden,
    InvitationInvalid,
    QuotaExceeded,
    WrongEmail,
    token_hash,
)
from nettriage.application.permissions import Role

NOW = datetime.now(UTC)


def new_user(database: Database, email: str | None = None) -> UUID:
    with database.admin.begin() as connection:
        return add_user(connection, email)


def new_org(database: Database) -> tuple[UUID, UUID]:
    owner = new_user(database)
    return create_org(database.app_api, user_id=owner, name=f"Org {uuid4().hex[:8]}").id, owner


def invite(
    database: Database,
    org: UUID,
    owner: UUID,
    email: str,
    role: Role = "viewer",
    now: datetime = NOW,
) -> str:
    _, token = create_invitation(
        database.app_api, org, actor_id=owner, email=email, role=role, now=now
    )
    return token


def test_an_invitation_is_pending_and_only_its_tokens_hash_is_stored(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"

    invitation, token = create_invitation(
        database.app_api,
        org,
        actor_id=owner,
        email=email,
        role="analyst",
        now=NOW,
    )

    assert [i.id for i in list_invitations(database.app_api, org, owner, NOW)] == [invitation.id]
    assert (invitation.role, invitation.expires_at) == ("analyst", NOW + timedelta(days=7))
    with database.admin.begin() as connection:
        stored: str = connection.execute(
            text("SELECT token_hash FROM invitations WHERE id = :id"), {"id": invitation.id}
        ).scalar_one()
    assert stored == token_hash(token)
    assert token not in stored


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_an_admin_can_invite_only_analysts_and_viewers(database: Database, role: Role) -> None:
    org, _ = new_org(database)
    admin = new_user(database)
    with database.admin.begin() as connection:
        add_member(connection, org, admin, "admin")

    with pytest.raises(Forbidden):
        create_invitation(
            database.app_api,
            org,
            actor_id=admin,
            email=f"{uuid4().hex}@example.com",
            role=role,
            now=NOW,
        )


def test_a_member_or_a_pending_address_can_not_be_invited_again(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    member = new_user(database, email.upper())
    with database.admin.begin() as connection:
        add_member(connection, org, member, "viewer")
    pending = f"{uuid4().hex}@example.com"
    invite(database, org, owner, pending)

    with pytest.raises(Conflict, match="already a member"):
        invite(database, org, owner, email)
    with pytest.raises(Conflict, match="pending invitation"):
        invite(database, org, owner, pending.upper())


def test_an_expired_invitation_is_replaced_by_a_new_one(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    invite(database, org, owner, email)
    later = NOW + timedelta(days=8)

    invite(database, org, owner, email, now=later)

    assert len(list_invitations(database.app_api, org, owner, later)) == 1


def test_an_org_has_at_most_twenty_pending_invitations(database: Database) -> None:
    org, owner = new_org(database)
    with database.admin.begin() as connection:
        for _ in range(20):
            add_invitation(connection, org, owner, f"{uuid4().hex}@example.com")

    with pytest.raises(QuotaExceeded, match="20 pending"):
        invite(database, org, owner, f"{uuid4().hex}@example.com")


def test_members_and_pending_invitations_together_stay_within_ten(database: Database) -> None:
    org, owner = new_org(database)
    with database.admin.begin() as connection:
        for _ in range(8):
            add_member(connection, org, add_user(connection), "viewer")
    invite(database, org, owner, f"{uuid4().hex}@example.com")  # 9 members + 1 pending

    with pytest.raises(QuotaExceeded, match="10 members"):
        invite(database, org, owner, f"{uuid4().hex}@example.com")


def test_a_revoked_invitation_is_gone_and_can_not_be_revoked_twice(database: Database) -> None:
    org, owner = new_org(database)
    invitation, _ = create_invitation(
        database.app_api,
        org,
        actor_id=owner,
        email=f"{uuid4().hex}@example.com",
        role="viewer",
        now=NOW,
    )

    first = revoke_invitation(
        database.app_api, org, actor_id=owner, invitation_id=invitation.id, now=NOW
    )
    again = revoke_invitation(
        database.app_api, org, actor_id=owner, invitation_id=invitation.id, now=NOW
    )

    assert (first, again) == (True, False)
    assert list_invitations(database.app_api, org, owner, NOW) == []


def test_accepting_joins_the_org_with_the_invited_role(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email, role="analyst")
    invitee = new_user(database, email.upper())

    joined = accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)

    assert (joined.id, joined.role) == (org, "analyst")
    assert role_of(database.app_api, org, invitee) == "analyst"
    assert list_invitations(database.app_api, org, owner, NOW) == []


def test_an_invitation_is_for_its_email_address_only(database: Database) -> None:
    org, owner = new_org(database)
    token = invite(database, org, owner, f"{uuid4().hex}@example.com")

    with pytest.raises(WrongEmail):
        accept_invitation(database.app_api, user_id=new_user(database), token=token, now=NOW)


def test_an_invitation_works_once(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email)
    invitee = new_user(database, email)
    accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)

    with pytest.raises(InvitationInvalid):
        accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)


@pytest.mark.parametrize("state", ["revoked", "expired", "unknown"])
def test_a_revoked_expired_or_unknown_invitation_is_refused(database: Database, state: str) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    invitation, token = create_invitation(
        database.app_api,
        org,
        actor_id=owner,
        email=email,
        role="viewer",
        now=NOW,
    )
    when = NOW
    if state == "revoked":
        revoke_invitation(
            database.app_api, org, actor_id=owner, invitation_id=invitation.id, now=NOW
        )
    elif state == "expired":
        when = NOW + timedelta(days=7)
    else:
        token = "never-issued"  # noqa: S105 - a made-up token, not a password

    with pytest.raises(InvitationInvalid):
        accept_invitation(
            database.app_api, user_id=new_user(database, email), token=token, now=when
        )


def test_a_user_in_three_orgs_can_not_join_a_fourth(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email)
    invitee = new_user(database, email)
    for number in range(3):
        create_org(database.app_api, user_id=invitee, name=f"Theirs {number}")

    with pytest.raises(QuotaExceeded, match="at most 3"):
        accept_invitation(database.app_api, user_id=invitee, token=token, now=NOW)


def test_a_full_org_takes_no_one_else(database: Database) -> None:
    org, owner = new_org(database)
    email = f"{uuid4().hex}@example.com"
    token = invite(database, org, owner, email)
    with database.admin.begin() as connection:
        for _ in range(9):
            add_member(connection, org, add_user(connection), "viewer")

    with pytest.raises(QuotaExceeded, match="already has 10"):
        accept_invitation(database.app_api, user_id=new_user(database, email), token=token, now=NOW)
    assert get_org(database.app_api, org, owner).member_count == 10


def test_only_the_tokens_hash_reveals_an_invitation_to_a_non_member(database: Database) -> None:
    org, owner = new_org(database)
    token = invite(database, org, owner, f"{uuid4().hex}@example.com")
    stranger = new_user(database)

    def visible(hash_setting: str | None) -> int:
        with tenant_transaction(database.app_api, user_id=stranger) as connection:
            if hash_setting is not None:
                connection.execute(
                    text("SELECT set_config('app.invitation_token_hash', :h, true)"),
                    {"h": hash_setting},
                )
            count: int = connection.execute(text("SELECT count(*) FROM invitations")).scalar_one()
        return count

    assert (visible(None), visible(token_hash("guess")), visible(token_hash(token))) == (0, 0, 1)
