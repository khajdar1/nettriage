"""The permission table and the no-escalation rule (spec §6.4)."""

import pytest

from nettriage.application.permissions import PERMISSIONS, ROLES, Role, allows, can_manage

# Spec §6.4's table, written out by hand: who has each permission.
SPEC = {
    "org:read": "owner admin analyst viewer",
    "members:read": "owner admin analyst viewer",
    "uploads:read": "owner admin analyst viewer",
    "findings:read": "owner admin analyst viewer",
    "uploads:create": "owner admin analyst",
    "findings:triage": "owner admin analyst",
    "findings:comment": "owner admin analyst",
    "ai:request": "owner admin analyst",
    "ai:feedback": "owner admin analyst",
    "members:invite": "owner admin",
    "members:role": "owner admin",
    "members:remove": "owner admin",
    "org:update": "owner admin",
    "audit:read": "owner admin",
    "usage:read": "owner admin",
    "org:delete": "owner",
}


def test_the_table_is_exactly_the_spec() -> None:
    assert set(PERMISSIONS) == set(SPEC)
    for permission, holders in SPEC.items():
        assert {role for role in ROLES if allows(role, permission)} == set(holders.split())


def test_an_unknown_permission_is_an_error_not_a_silent_no() -> None:
    with pytest.raises(KeyError):
        allows("owner", "org:destroy")


@pytest.mark.parametrize(
    ("actor", "manages"),
    [
        ("owner", {"owner", "admin", "analyst", "viewer"}),
        ("admin", {"analyst", "viewer"}),
        ("analyst", set()),
        ("viewer", set()),
    ],
)
def test_owners_manage_anyone_and_admins_only_analysts_and_viewers(
    actor: Role, manages: set[str]
) -> None:
    assert {target for target in ROLES if can_manage(actor, target)} == manages
