# 0009: No VPC in production

- Status: Accepted
- Date: 2026-09-26

## Context
Every AWS service the app talks to (DynamoDB, S3, Bedrock, Cognito) is reachable over its
public, TLS-protected, IAM-authenticated endpoint. Neon is also reached over the public
internet with TLS. The traditional reason to put Lambda in a VPC is network segmentation
toward these services, but that segmentation comes with always-on costs.

## Decision
No VPC for any application component in production. Every network hop is authenticated
(IAM, SigV4, database credentials) and TLS-encrypted regardless of network placement.
Hands-on network engineering is deliberately out of scope here and lives in the Milestone 4
live lab, which stands up its own throwaway VPC with a decoy instance.

## Consequences
- No NAT gateway and no VPC interface endpoint costs, and no network perimeter to configure,
  patch or monitor.
- Neon's endpoint being reachable from the public internet is an accepted risk, mitigated as
  described in ADR 0002 (TLS, per-role passwords) rather than by network isolation.

## Alternatives considered
- **Lambda in a VPC with a NAT gateway:** about $33/month for the NAT gateway alone, before
  any other resource.
- **Lambda in a VPC with interface endpoints instead of a NAT gateway:** about $7.30 per
  availability zone per month, per endpoint. Multiple endpoints (S3, DynamoDB, etc.) across
  multiple AZs add up quickly for a project targeting near-$0 cost.
