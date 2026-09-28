# 0003: CloudFront to a Lambda Function URL with Origin Access Control

- Status: Accepted
- Date: 2026-09-26

## Context
The API needs a path from the public edge to a Lambda-backed FastAPI app, at $0 steady-state
cost, without giving up the option to stream responses once Milestone 2 adds chat. It also
needs to stay closed to anyone who bypasses the edge.

## Decision
CloudFront signs `/api/*` requests with SigV4, through Origin Access Control, to a Lambda
Function URL configured for `AWS_IAM` auth. There is no API Gateway in the request path.

## Consequences
- Cost is $0, and the Function URL already supports `RESPONSE_STREAM` invoke mode for
  Milestone 2; Milestone 1 uses `BUFFERED`.
- A direct call to the Function URL, without a valid CloudFront signature, gets `403`. The
  function's resource policy allows only this CloudFront distribution to invoke it.
- Requests with a body must carry an `x-amz-content-sha256` header for the signature to
  verify; the frontend's API client computes it.
- There is no API-gateway-level throttling, so request limits are enforced in the application
  (GCRA rate limiting, ADR 0006) and, later, by a WAF in front of CloudFront.

## Alternatives considered
- **API Gateway HTTP API:** about $1 per million requests, and no response streaming.
- **API Gateway REST API:** about $3.50 per million requests, with more configuration.
