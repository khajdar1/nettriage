"""Triage rules (spec §7): a finding's status and assignee change only against the version the
caller last read (optimistic concurrency), and only a member who can triage (owner, admin or
analyst) can be its assignee (the owner's decision, Plan 4c)."""

from __future__ import annotations

import re

from nettriage.application.organizations import OrgRuleError
from nettriage.application.permissions import Role, allows

MAX_COMMENT = 2000
# The ETags this API sends: the finding's version in quotes, such as "3".
_ETAG = re.compile(r'"([1-9][0-9]{0,9})"')


class StaleVersion(OrgRuleError):
    """The finding changed since the caller read it: someone else triaged it first."""

    def __init__(self, current: int) -> None:
        super().__init__("This finding changed since you read it. Reload it and try again.")
        self.current = current


class InvalidAssignee(OrgRuleError):
    """Only a member who can triage the finding can be its assignee."""


def etag(version: int) -> str:
    return f'"{version}"'


def expected_version(if_match: str | None) -> int | None:
    """The version an `If-Match` header names: exactly one ETag as this API sends it. None for a
    missing header, `*`, a weak or malformed ETag, or a list: none of them says which version
    the caller read."""
    if if_match is None:
        return None
    match = _ETAG.fullmatch(if_match.strip())
    return int(match[1]) if match else None


def can_be_assigned(role: Role | None) -> bool:
    return role is not None and allows(role, "findings:triage")
