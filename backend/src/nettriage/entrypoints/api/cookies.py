"""The cookies (spec §6.2): Secure, HttpOnly, SameSite=Lax, Path=/, no Domain.

- `__Host-session` holds the session ID, and lives at most as long as the session can.
- `__Host-sign-in` binds a sign-in to the browser that started it: it holds the sign-in's
  `state` for 15 minutes, and the callback accepts only a state equal to it. Without it, an
  attacker could send a victim the callback link of the attacker's own sign-in (login CSRF).
"""

from fastapi import Response

from nettriage.application.sessions import ABSOLUTE_TIMEOUT, COOKIE_NAME
from nettriage.application.sign_in import SIGN_IN_WINDOW

SIGN_IN_COOKIE = "__Host-sign-in"
SIGN_IN_MAX_AGE = int(SIGN_IN_WINDOW.total_seconds())
EXPIRED_SESSION_COOKIE = f"{COOKIE_NAME}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"
EXPIRED_SIGN_IN_COOKIE = f"{SIGN_IN_COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session_id,
        max_age=int(ABSOLUTE_TIMEOUT.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.headers.append("Set-Cookie", EXPIRED_SESSION_COOKIE)


def set_sign_in_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        SIGN_IN_COOKIE,
        state,
        max_age=SIGN_IN_MAX_AGE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_sign_in_cookie(response: Response) -> None:
    response.headers.append("Set-Cookie", EXPIRED_SIGN_IN_COOKIE)
