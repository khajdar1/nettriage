# 0006: A GCRA distributed rate limiter

- Status: Accepted
- Date: 2026-09-26

## Context
Lambda instances are stateless and run concurrently, so an in-process rate limiter would let
each instance track its own counter and let bursts through undetected. Rate limits are keyed
by different subjects depending on the route (user ID, client IP or organization ID), and
some policies allow a small burst above the steady rate, which a plain fixed-window counter
handles poorly.

## Decision
Rate limiting uses GCRA (Generic Cell Rate Algorithm) implemented on DynamoDB: each key
stores one theoretical arrival time (`tat`), updated with a conditional write on every check.
Limited routes return the IETF `RateLimit-Policy` and `RateLimit` headers; a blocked request
gets `429` with `Retry-After`.

## Consequences
- GCRA is exact under concurrency: two Lambda instances checking the same key race on the
  same conditional write, so one of them always loses cleanly rather than both allowing a
  request that together exceeds the limit.
- Each check costs one read and one write against DynamoDB.
- On a write conflict, the check retries up to 3 times; if it still can't decide, it fails
  open: the request is allowed, and the failure is logged, counted and alerted on.

## Alternatives considered
- **Fixed-window counters:** simple, but they double the effective burst at window edges (a
  client can send the full limit at the end of one window and the full limit again at the
  start of the next).
- **Sliding logs:** exact, but storing every request timestamp per key is heavy for a
  Always-Free-tier storage budget.
- **Gateway-level throttling:** available in API Gateway, but it throttles at the account or
  route level, not per user, IP or organization, which is what the policies need.
