"""Rate limiting with GCRA (spec §6.5).

For `limit` requests per `period`, the emission interval is T = period / limit and the burst
tolerance is tau = (burst - 1) * T. A request at `now` is allowed if tat - now <= tau, where
`tat` is the key's theoretical arrival time (`now` for a new or idle key); then
tat = max(tat, now) + T.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Policy:
    name: str
    limit: int
    period: timedelta
    burst: int

    @property
    def period_ms(self) -> int:
        return int(self.period.total_seconds() * 1000)

    @property
    def interval_ms(self) -> int:
        return self.period_ms // self.limit

    @property
    def tolerance_ms(self) -> int:
        return (self.burst - 1) * self.interval_ms


POLICIES: dict[str, Policy] = {
    policy.name: policy
    for policy in (
        Policy("api.user", 120, timedelta(minutes=1), 30),
        Policy("api.mutation.user", 30, timedelta(minutes=1), 10),
        Policy("auth.ip", 10, timedelta(minutes=1), 5),
        Policy("public.ip", 60, timedelta(minutes=1), 20),
        Policy("uploads.org", 20, timedelta(days=1), 5),
        Policy("invites.org", 20, timedelta(days=1), 5),
        Policy("ai.rerun.user", 10, timedelta(hours=1), 3),
    )
}


# Not one of §6.5's policies, and not stored in DynamoDB: a per-process guard on how many
# unknown session IDs one IP may present before the API stops spending a DynamoDB read on each.
UNKNOWN_SESSIONS_PER_IP = Policy("unknown-session.ip", 10, timedelta(minutes=1), 5)


class LocalLimiter:
    """GCRA kept in this process's memory. It costs nothing to check, but each Lambda instance
    counts on its own, so it only guards what must not cost a DynamoDB call."""

    MAX_SUBJECTS = 10_000

    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self._tats: dict[str, int] = {}

    def exhausted(self, subject: str, now_ms: int) -> bool:
        tat = self._tats.get(subject)
        return tat is not None and tat - now_ms > self.policy.tolerance_ms

    def hit(self, subject: str, now_ms: int) -> None:
        if len(self._tats) >= self.MAX_SUBJECTS:
            self._tats.clear()
        self._tats[subject] = max(self._tats.get(subject, now_ms), now_ms) + self.policy.interval_ms


@dataclass(frozen=True)
class Decision:
    policy: Policy
    allowed: bool
    remaining: int = 0
    reset_seconds: int = 0
    retry_after_seconds: int = 0
    # True when the limiter couldn't decide and let the request through (fail open).
    degraded: bool = False


def allowed(policy: Policy, tat_ms: int, now_ms: int) -> Decision:
    """The decision after an allowed request moved the key's tat to `tat_ms`."""
    headroom = policy.tolerance_ms - (tat_ms - now_ms)
    remaining = headroom // policy.interval_ms + 1 if headroom >= 0 else 0
    return Decision(
        policy=policy,
        allowed=True,
        remaining=remaining,
        reset_seconds=math.ceil(max(0, tat_ms - now_ms) / 1000),
    )


def limited(policy: Policy, tat_ms: int, now_ms: int) -> Decision:
    """The decision for a request refused because the key's tat is `tat_ms`."""
    return Decision(
        policy=policy,
        allowed=False,
        reset_seconds=math.ceil(max(0, tat_ms - now_ms) / 1000),
        retry_after_seconds=max(1, math.ceil((tat_ms - policy.tolerance_ms - now_ms) / 1000)),
    )


def failed_open(policy: Policy) -> Decision:
    return Decision(policy=policy, allowed=True, degraded=True)


def header_values(decisions: Iterable[Decision]) -> dict[str, str]:
    """The IETF RateLimit-Policy and RateLimit fields (draft-ietf-httpapi-ratelimit-headers),
    one list member per policy that was checked. Fail-open decisions have nothing to report."""
    known = [decision for decision in decisions if not decision.degraded]
    if not known:
        return {}
    return {
        "RateLimit-Policy": ", ".join(
            f'"{d.policy.name}";q={d.policy.limit};w={d.policy.period_ms // 1000}' for d in known
        ),
        "RateLimit": ", ".join(
            f'"{d.policy.name}";r={d.remaining};t={d.reset_seconds}' for d in known
        ),
    }


def viewer_ip(viewer_address: str | None) -> str | None:
    """The client's IP from CloudFront's `CloudFront-Viewer-Address` header ("ip:port"). None
    if the header is missing or malformed."""
    if not viewer_address or ":" not in viewer_address:
        return None
    host = viewer_address.rsplit(":", 1)[0].strip("[]")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def ip_subject(viewer_address: str | None) -> str | None:
    """The rate-limit subject for a client IP. IPv6 clients are limited per /64, because one
    host can use a whole /64. None without a viewer address: requests that didn't come through
    CloudFront (local runs, the Lambda adapter's readiness check) aren't IP-limited."""
    ip = viewer_ip(viewer_address)
    if ip is None or ":" not in ip:
        return ip
    return str(ipaddress.ip_network(f"{ip}/64", strict=False))
