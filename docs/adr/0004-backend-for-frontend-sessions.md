# 0004: Backend-for-frontend sessions instead of tokens in the browser

- Status: Accepted
- Date: 2026-09-26

## Context
The frontend is a React SPA, which means any XSS bug runs attacker script in the same origin
as application code. Cognito issues ID, access and refresh tokens; if the browser holds any
of them, an XSS bug can exfiltrate a token and impersonate the user until it expires.

## Decision
The API runs the OIDC authorization-code flow with PKCE (S256) against Cognito and keeps its
own server-side session behind a `__Host-session` cookie (`Secure; HttpOnly; SameSite=Lax;
Path=/`, no `Domain`). Cognito's ID, access and refresh tokens are verified once at login and
then discarded; the browser never sees them.

## Consequences
- Cross-site scripting can no longer steal a usable credential: the cookie is `HttpOnly`, and
  the server holds only the session's SHA-256 hash, not the raw value.
- Because the browser now automatically attaches the session cookie to same-site requests,
  CSRF defenses are mandatory: an `X-CSRF-Token` header checked against the session's stored
  token, `Sec-Fetch-Site` validation, and `SameSite=Lax` as a second layer.
- A server-side session store is now required to hold session, sign-in and CSRF state; that
  store is DynamoDB (ADR 0005).
- Sessions expire after 60 idle minutes or 12 hours total, and a new session is created on
  every login, so a stolen cookie has a bounded lifetime.

## Alternatives considered
- **SPA holding tokens in memory or localStorage:** removes the need for a session store and
  matches common SPA patterns, but any XSS bug directly exposes long-lived, replayable
  tokens, and there is no server-side way to revoke a token before it expires.
