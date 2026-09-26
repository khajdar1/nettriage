# 0001: Serverless on AWS Always Free

- Status: Accepted
- Date: 2026-09-26

## Context
The project runs on a credit-based AWS account with a target steady-state cost of about
$0/month, and in any case no more than $1/month. Always-on infrastructure dominates small
bills even at low traffic: a NAT gateway costs about $33/month, a load balancer about
$16/month, each public IPv4 address $3.65/month, and the smallest RDS instance about
$12/month. Any of these alone would blow the budget.

## Decision
Build entirely on services with an Always Free tier and no always-on compute: Lambda, SQS,
DynamoDB, Cognito, CloudFront and S3. The API Lambda runs Python 3.14 on arm64.

## Consequences
- Steady-state cost is about $0-1/month.
- Lambda cold starts add roughly a second of latency on an idle path; handlers are stateless
  so any instance can serve any request.
- The Python 3.13 runtime stays the documented fallback if a dependency ever lacks a 3.14
  arm64 wheel. It wasn't needed in practice: every backend dependency resolved and installed
  cleanly for Python 3.14 on `aarch64-manylinux`, so the Lambda runtime is `python3.14` on
  `arm64`, matching the rest of the design.
- Everything must fit Lambda's request/response model; long-lived connections and background
  processing move to SQS-triggered workers (ADR 0007) instead of a persistent process.

## Alternatives considered
- **One EC2 instance with Docker Compose:** about $6/month now, rising to about $18/month
  once the current free-tier credit period ends in 2027, plus patching and process
  supervision the team would own.
- **EKS:** about $73/month for the control plane alone, before any worker nodes.
- **Fargate behind a load balancer:** about $30/month, mostly the load balancer.
- **App Runner:** closed to new customers, so not available to build on.
