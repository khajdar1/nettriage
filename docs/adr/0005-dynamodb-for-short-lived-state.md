# 0005: DynamoDB for short-lived state

- Status: Accepted
- Date: 2026-09-26

## Context
Sessions, sign-in state, rate-limit counters, AI spend budgets and idempotency keys are all
short-lived, high-churn, and read/written on nearly every request. They need atomic
conditional updates (so two concurrent requests can't both "win" a rate-limit check or a
budget reservation) without adding load, or a network hop to a database that can sleep, to
every request. DynamoDB's Always Free tier provides 25 read and 25 write capacity units per
region at $0.

## Decision
A single `runtime` DynamoDB table, with TTL, holds sessions, sign-in state, rate-limit keys,
AI budgets and idempotency keys. It runs on provisioned capacity within Always Free: `prod`
gets 10 RCU / 10 WCU, `dev` gets 3 RCU / 3 WCU.

## Consequences
- Conditional writes give atomic single-digit-millisecond updates with no row-lock
  contention, at $0.
- Provisioned (not on-demand) capacity means throughput is capped at those fixed unit counts;
  a spike beyond it throttles rather than auto-scales.
- Failure policy differs by item: rate limits fail open (the request is served, and the
  failure is logged and alerted on); sessions and AI budgets fail closed (a request is
  rejected, or no Bedrock call is made, if the state can't be read or written).

## Alternatives considered
- **Postgres tables in Neon:** would add row-lock contention under concurrent updates, and
  every rate-limit or budget check would wake a database that can otherwise stay idle.
- **Upstash Redis:** a free tier exists, but it caps at 500,000 commands a month and adds
  another vendor and another set of credentials to manage.
- **ElastiCache:** no meaningful free tier; the smallest usable configuration costs at least
  about $6/month, which alone would exceed the project's budget.
