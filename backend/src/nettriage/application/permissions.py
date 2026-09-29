"""Who may do what in an organization (spec §6.4). Deny by default: a permission not in the
table is an error, and a role not listed for a permission doesn't have it."""

from __future__ import annotations

from typing import Literal, get_args

Role = Literal["owner", "admin", "analyst", "viewer"]
ROLES: tuple[Role, ...] = get_args(Role)

_EVERYONE: frozenset[Role] = frozenset(ROLES)
_CONTRIBUTORS: frozenset[Role] = frozenset({"owner", "admin", "analyst"})
_MANAGERS: frozenset[Role] = frozenset({"owner", "admin"})
_OWNERS: frozenset[Role] = frozenset({"owner"})

PERMISSIONS: dict[str, frozenset[Role]] = {
    "org:read": _EVERYONE,
    "members:read": _EVERYONE,
    "uploads:read": _EVERYONE,
    "findings:read": _EVERYONE,
    "uploads:create": _CONTRIBUTORS,
    "findings:triage": _CONTRIBUTORS,
    "findings:comment": _CONTRIBUTORS,
    "ai:request": _CONTRIBUTORS,
    "ai:feedback": _CONTRIBUTORS,
    "members:invite": _MANAGERS,
    "members:role": _MANAGERS,
    "members:remove": _MANAGERS,
    "org:update": _MANAGERS,
    "audit:read": _MANAGERS,
    "usage:read": _MANAGERS,
    "org:delete": _OWNERS,
}

# Whose membership each role may grant, change or remove: owners anyone, admins only analysts
# and viewers (spec §6.4). Nobody can grant a role above their own.
_MANAGEABLE: dict[Role, frozenset[Role]] = {
    "owner": _EVERYONE,
    "admin": frozenset({"analyst", "viewer"}),
    "analyst": frozenset(),
    "viewer": frozenset(),
}


def allows(role: Role, permission: str) -> bool:
    return role in PERMISSIONS[permission]


def can_manage(actor: Role, target: Role) -> bool:
    """Whether `actor` may invite someone as `target`, or change or remove a `target` member."""
    return target in _MANAGEABLE[actor]
