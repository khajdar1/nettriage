"""Organization rules (spec §5.2, §5.7, §6.3): quotas, slugs, invitation tokens and emails."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from datetime import timedelta

from nettriage.application.permissions import Role, allows, can_manage

MAX_MEMBERS_PER_ORG = 10
MAX_ORGS_PER_USER = 3
MAX_PENDING_INVITATIONS = 20
INVITATION_LIFETIME = timedelta(days=7)
MAX_SLUG = 60
MAX_EMAIL = 320
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def slugify(name: str) -> str:
    """A slug from an org's name: lowercase ASCII words joined by dashes, at most 50 characters
    (leaving room for a suffix), or "org" if nothing is left."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_name.lower())
    slug = "-".join(words)[:50].strip("-")
    return slug or "org"


def with_suffix(slug: str) -> str:
    """The slug with a random suffix, for when the plain one is taken."""
    return f"{slug[: MAX_SLUG - 7]}-{secrets.token_hex(3)}"


def new_invitation_token() -> str:
    """32 random bytes, base64url-encoded; shown once in the invitation link (spec §6.3)."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invitation_url(app_origin: str, token: str) -> str:
    """The token goes in the fragment, which browsers never send to servers (spec §6.3)."""
    return f"{app_origin}/invite#{token}"


def normalize_email(value: str) -> str | None:
    """The address as typed, trimmed; None if it can't be an email address."""
    email = value.strip()
    if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email):
        return None
    return email


class OrgRuleError(Exception):
    """A request the organization rules refuse. The message is safe to show to the caller."""


class NotFound(OrgRuleError):
    """No such member or invitation in this organization."""


class Forbidden(OrgRuleError):
    """The caller's role doesn't allow this (spec §6.4's no-escalation rules)."""


class LastOwner(OrgRuleError):
    """An organization always keeps at least one owner (spec §6.4)."""


class QuotaExceeded(OrgRuleError):
    """One of §5.7's limits: 3 orgs per user, 10 members per org, 20 pending invitations."""


class Conflict(OrgRuleError):
    """The request clashes with what exists: already a member, already invited."""


class InvitationInvalid(OrgRuleError):
    """The invitation doesn't exist, was used or revoked, or has expired."""


class WrongEmail(OrgRuleError):
    """The invitation is for a different email address."""


class ConfirmationMismatch(OrgRuleError):
    """Deleting an org needs its exact name as confirmation (spec §7)."""


def check_role_change(
    *, actor_id: object, actor_role: Role, target_id: object, current: Role, new: Role
) -> None:
    """No escalation (spec §6.4): nobody changes their own role, and a manager changes only
    roles they may manage, to roles they may grant."""
    if actor_id == target_id:
        raise Forbidden("You can't change your own role.")
    if not can_manage(actor_role, current) or not can_manage(actor_role, new):
        raise Forbidden("Your role can't grant or change that role.")


def require(role: Role, permission: str) -> None:
    """Refuse a change the caller's role, as it is now, doesn't allow."""
    if not allows(role, permission):
        raise Forbidden("Your role in this organization doesn't allow that.")
