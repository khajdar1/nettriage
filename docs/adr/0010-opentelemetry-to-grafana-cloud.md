# 0010: OpenTelemetry to Grafana Cloud

- Status: Accepted
- Date: 2026-09-26

## Context
Every request crosses several Lambda functions (`api`, `analyze`, `triage`, `ops`), so
diagnosing a problem needs traces and metrics that survive across a serverless, short-lived
process without the team operating its own telemetry backend.

## Decision
The app is instrumented with the OpenTelemetry SDK, which exports to the OpenTelemetry
Lambda collector layer (using the decouple processor to avoid blocking the response), which
forwards to Grafana Cloud's free tier. Milestone 1 ships traces and metrics; logs stay as
structured JSON to stdout and CloudWatch (7-day retention) and are added to Grafana (Loki) in
Plan 7.

## Consequences
- Instrumentation is vendor-neutral: switching telemetry backends later means changing an
  exporter endpoint, not the instrumentation calls throughout the code.
- Grafana Cloud's free tier retains telemetry for 14 days.
- The GenAI semantic conventions used to label LLM spans are still marked "development" by
  OpenTelemetry, so their package versions are pinned to avoid an unannounced breaking change.

## Alternatives considered
- **CloudWatch plus X-Ray:** native to AWS, but the X-Ray SDKs reach end of support in
  February 2027, and CloudWatch Transaction Search (needed for trace search) is a paid
  feature.
- **Honeycomb:** a capable tracing backend, but its free tier and this project's telemetry
  volume were less well matched than Grafana Cloud's.
- **Sentry:** strong for error tracking, but not built around traces and metrics the way this
  project needs from day one.
