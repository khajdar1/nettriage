"""Who may call a route (spec §6.2, §6.4, §6.5). Deny by default: every route declares
`Public(policy)` or `SignedIn`, and tests/security/test_route_access.py fails on any route that
declares neither.

- `Public(policy)` rate-limits by client IP.
- `SignedIn` needs a valid session. It fails closed: an unreadable session store gives 503.
  State-changing methods also pass the CSRF checks. Requests are rate-limited per user, and
  state-changing ones also by `api.mutation.user`.

The edge lets any request with a `__Host-session` cookie through, so bogus cookies must not
each cost a DynamoDB read: a value that can't be a session ID is refused unread, and an IP that
keeps presenting unknown sessions is refused unread for a while (`UNKNOWN_SESSIONS_PER_IP`).
"""

from __future__ import annotations

import hmac
import logging
import re
from typing import Annotated, NoReturn
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, HTTPException, Request

from nettriage.adapters.runtime_table import epoch_millis
from nettriage.application.rate_limits import POLICIES, Policy, ip_subject
from nettriage.application.sessions import COOKIE_NAME, Session
from nettriage.entrypoints.api.auditing import VIEWER_ADDRESS, audit
from nettriage.entrypoints.api.cookies import EXPIRED_SESSION_COOKIE
from nettriage.entrypoints.api.services import Services, get_services

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{43}")  # 32 bytes, base64url without padding
SAME_SITE = frozenset({"same-origin", "none"})


class Access:
    """A route's access declaration."""


class Public(Access):
    def __init__(self, policy: str) -> None:
        self.policy = POLICIES[policy]

    def __call__(self, request: Request) -> None:
        subject = ip_subject(request.headers.get(VIEWER_ADDRESS))
        if subject is not None:
            enforce(request, self.policy, subject, actor=None)


class SignedIn(Access):
    def __call__(self, request: Request) -> Session:
        services = get_services(request)
        session = _valid_session(request, services)
        if request.method not in SAFE_METHODS:
            _check_csrf(request, services, session)
        enforce(request, POLICIES["api.user"], str(session.user_id), actor=session.user_id)
        if request.method not in SAFE_METHODS:
            enforce(
                request, POLICIES["api.mutation.user"], str(session.user_id), actor=session.user_id
            )
        return _touched(services, session)


signed_in = SignedIn()
CurrentSession = Annotated[Session, Depends(signed_in)]


def unauthorized() -> HTTPException:
    """401 that also clears the browser's stale session cookie."""
    return HTTPException(401, headers={"Set-Cookie": EXPIRED_SESSION_COOKIE})


def unavailable(what: str) -> HTTPException:
    return HTTPException(503, detail=f"{what} is unavailable right now; try again shortly.")


def enforce(request: Request, policy: Policy, subject: str, *, actor: UUID | None) -> None:
    """Check one rate limit, and remember the decision for the RateLimit headers."""
    services = get_services(request)
    decision = services.rate_limiter.check(policy, subject)
    request.state.rate_limits = [*getattr(request.state, "rate_limits", []), decision]
    if decision.degraded:
        services.metrics.rate_limit_errors.add(1, {"policy": policy.name})
        return
    if decision.allowed:
        return
    services.metrics.rate_limited.add(1, {"policy": policy.name})
    if services.rate_limiter.should_audit(policy, subject):
        audit(
            request,
            action="ratelimit.limited",
            outcome="denied",
            actor_user_id=actor,
            details={"policy": policy.name},
        )
    raise HTTPException(
        429,
        detail="Too many requests; try again later.",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _valid_session(request: Request, services: Services) -> Session:
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise unauthorized()
    subject = ip_subject(request.headers.get(VIEWER_ADDRESS))
    now = epoch_millis(services.clock())
    if subject is not None and services.unknown_sessions.exhausted(subject, now):
        raise unauthorized()
    if not SESSION_ID.fullmatch(session_id):
        _unknown_session(services, subject, now)
    try:
        session = services.sessions.get(session_id)
    except BotoCoreError, ClientError:
        logger.exception("session_read_failed")
        raise unavailable("Signing in") from None
    if session is None:
        _unknown_session(services, subject, now)
    if session.is_expired(services.clock()):
        try:
            services.sessions.delete(session)
        except BotoCoreError, ClientError:
            logger.warning("expired_session_delete_failed")
        raise unauthorized()
    return session


def _unknown_session(services: Services, subject: str | None, now: int) -> NoReturn:
    if subject is not None:
        services.unknown_sessions.hit(subject, now)
    raise unauthorized()


def _check_csrf(request: Request, services: Services, session: Session) -> None:
    site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    token = request.headers.get("x-csrf-token", "")
    if (
        site in SAME_SITE
        and (origin is None or origin == services.app_origin)
        and hmac.compare_digest(token.encode(), session.csrf_token.encode())
    ):
        return
    services.metrics.csrf_failed.add(1)
    logger.warning("csrf_failed", extra={"route": request.url.path})
    raise HTTPException(403, detail="The request failed its CSRF check. Reload the page and retry.")


def _touched(services: Services, session: Session) -> Session:
    now = services.clock()
    if not session.needs_touch(now):
        return session
    try:
        touched = services.sessions.touch(session, now)
    except BotoCoreError, ClientError:
        logger.warning("session_touch_failed")
        return session
    if touched is None:
        raise unauthorized()
    return touched
