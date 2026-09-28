# 0006: A GCRA distributed rate limiter

- Status: Accepted; storage amended 2026-09-28 (Plan 3b)
- Date: 2026-09-26

## Context
Lambda instances are stateless and run concurrently, so an in-process rate limiter would let
each instance track its own counter and let bursts through undetected. Rate limits are keyed
by different subjects depending on the route (user ID, client IP or organization ID), and
some policies allow a small burst above the steady rate, which a plain fixed-window counter
handles poorly.

## Decision
Rate limiting uses GCRA (Generic Cell Rate Algorithm) implemented on DynamoDB: each key
stores one theoretical arrival time (`tat`), changed only by conditional updates. A new or idle
key is set to now + T; otherwise `tat` grows by T on condition that `tat - now <= tau`. A failed
condition returns the stored item, which says whether the request is over the limit.
Limited routes return the IETF `RateLimit-Policy` and `RateLimit` headers; a blocked request
gets `429` with `Retry-After`.

## Consequences
- GCRA is exact under concurrency: DynamoDB applies conditional updates of one item one at a
  time, so parallel requests can't both take the last slot. A test sends 100 parallel requests
  at DynamoDB Local in CI and expects exactly the burst to pass.
- Each check costs one or two writes and no reads: one for a new or idle key, two for an active
  key (the first condition fails, the second update succeeds), and one for a refused request.
- The first design (never built) read the item and then wrote it conditionally, retrying 3
  times on a conflict. Under contention it would run out of retries and fail open, letting more
  requests through than the limit allows, so Plan 3b replaced it before implementing it.
- If DynamoDB fails, the check fails open: the request is allowed, and the failure is logged
  and counted.

## Alternatives considered
- **Fixed-window counters:** simple, but they double the effective burst at window edges (a
  client can send the full limit at the end of one window and the full limit again at the
  start of the next).
- **Sliding logs:** exact, but storing every request timestamp per key is heavy for a
  Always-Free-tier storage budget.
- **Gateway-level throttling:** available in API Gateway, but it throttles at the account or
  route level, not per user, IP or organization, which is what the policies need.
