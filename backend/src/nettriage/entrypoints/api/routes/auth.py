"""Sign-in and sign-out (spec §4.1, §6.2), the backend-for-frontend way: the browser only ever
holds an opaque session cookie.

`login` and `callback` are browser navigations, so their failures redirect to the landing page
with `?sign_in=<reason>` instead of returning JSON.
"""

from __future__ import annotations

import hmac
import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from nettriage.adapters.oidc import OidcError
from nettriage.adapters.users import sign_in_user
from nettriage.application.rate_limits import viewer_ip
from nettriage.application.sessions import COOKIE_NAME, new_secret
from nettriage.application.sign_in import (
    LoginState,
    code_challenge,
    new_code_verifier,
    safe_return_to,
)
from nettriage.entrypoints.api.access import CurrentSession, Public, unavailable
from nettriage.entrypoints.api.auditing import VIEWER_ADDRESS, audit
from nettriage.entrypoints.api.cookies import (
    SIGN_IN_COOKIE,
    clear_session_cookie,
    clear_sign_in_cookie,
    set_session_cookie,
    set_sign_in_cookie,
)
from nettriage.entrypoints.api.services import Services, get_services

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")


class LogoutResult(BaseModel):
    logout_url: str


def redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})


def sign_in_failed(reason: str) -> RedirectResponse:
    return redirect(f"/?sign_in={reason}")


@router.get("/login", dependencies=[Depends(Public("auth.ip"))])
def login(request: Request, return_to: str | None = None) -> RedirectResponse:
    services = get_services(request)
    state, nonce, verifier = new_secret(), new_secret(), new_code_verifier()
    try:
        services.login_states.put(
            state, LoginState(verifier, nonce, safe_return_to(return_to)), services.clock()
        )
    except BotoCoreError, ClientError:
        logger.exception("login_state_write_failed")
        return sign_in_failed("unavailable")
    response = redirect(
        services.oidc.authorization_url(
            state=state, nonce=nonce, code_challenge=code_challenge(verifier)
        )
    )
    set_sign_in_cookie(response, state)
    return response


@router.get("/callback", dependencies=[Depends(Public("auth.ip"))])
def callback(
    request: Request, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    response = _finish_sign_in(request, code, state)
    clear_sign_in_cookie(response)
    return response


def _finish_sign_in(request: Request, code: str | None, state: str | None) -> RedirectResponse:
    services = get_services(request)
    started_here = request.cookies.get(SIGN_IN_COOKIE, "")
    if not state or not hmac.compare_digest(started_here.encode(), state.encode()):
        return sign_in_failed("expired")  # not started in this browser, or over 15 minutes ago
    try:
        login = services.login_states.take(state, services.clock())
    except BotoCoreError, ClientError:
        logger.exception("login_state_read_failed")
        return sign_in_failed("unavailable")
    if login is None:
        return sign_in_failed("expired")
    if not code:
        return sign_in_failed("failed")  # Cognito sent an error instead, e.g. a cancelled sign-in
    try:
        identity = services.oidc.identity(
            code=code, code_verifier=login.code_verifier, nonce=login.nonce
        )
    except OidcError as error:
        logger.warning("sign_in_failed", extra={"reason": str(error)})
        return sign_in_failed("failed")
    try:
        user = sign_in_user(services.database, sub=identity.sub, email=identity.email)
    except SQLAlchemyError:
        logger.exception("sign_in_user_failed")
        return sign_in_failed("unavailable")
    if user.disabled:
        audit(request, action="auth.session_created", outcome="denied", actor_user_id=user.user_id)
        return sign_in_failed("disabled")
    try:
        _end_session(services, request.cookies.get(COOKIE_NAME))  # never reuse a session
        session_id, _ = services.sessions.create(
            user_id=user.user_id,
            now=services.clock(),
            ip=viewer_ip(request.headers.get(VIEWER_ADDRESS)),
            user_agent=request.headers.get("user-agent"),
        )
    except BotoCoreError, ClientError:
        logger.exception("session_create_failed")
        return sign_in_failed("unavailable")
    if user.created:
        services.metrics.signups.add(1)
    audit(
        request,
        action="auth.session_created",
        outcome="success",
        actor_user_id=user.user_id,
        details={"new_user": user.created},
    )
    response = redirect(login.return_to)
    set_session_cookie(response, session_id)
    return response


@router.post("/logout")
def logout(request: Request, response: Response, session: CurrentSession) -> LogoutResult:
    services = get_services(request)
    try:
        services.sessions.delete(session)
    except BotoCoreError, ClientError:
        logger.exception("session_delete_failed")
        raise unavailable("Signing out") from None
    audit(request, action="auth.logout", outcome="success", actor_user_id=session.user_id)
    clear_session_cookie(response)
    return LogoutResult(logout_url=services.oidc.logout_url())


@router.post("/logout-all")
def logout_all(request: Request, response: Response, session: CurrentSession) -> LogoutResult:
    services = get_services(request)
    try:
        count = services.sessions.delete_all(session.user_id)
    except BotoCoreError, ClientError:
        logger.exception("session_delete_failed")
        raise unavailable("Signing out") from None
    audit(
        request,
        action="auth.logout_all",
        outcome="success",
        actor_user_id=session.user_id,
        details={"sessions": count},
    )
    clear_session_cookie(response)
    return LogoutResult(logout_url=services.oidc.logout_url())


def _end_session(services: Services, session_id: str | None) -> None:
    """Delete the session a browser brought to the callback, so a cookie planted before sign-in
    (session fixation) can never become a signed-in session."""
    if not session_id:
        return
    old = services.sessions.get(session_id)
    if old is not None:
        services.sessions.delete(old)
