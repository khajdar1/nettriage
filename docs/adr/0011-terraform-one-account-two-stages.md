# 0011: Terraform, one account, two stages

- Status: Accepted; amended by [ADR 0013](0013-owner-run-deploys-short-lived-credentials.md) on 2026-09-27
- Date: 2026-09-26

## Context
The project needs repeatable, reviewable infrastructure across a `dev` and a `prod` stage,
state locking that is safe for concurrent applies, and deploys that never rely on long-lived
AWS credentials.

## Decision
Terraform 1.11 or later, with native S3 state locking (`use_lockfile = true`, no DynamoDB lock
table). The owner applies a one-time bootstrap stack (the state bucket and budget alerts) with
`just bootstrap`; everything after that changes only through the reviewed, owner-run deploy
command (ADR 0013). `dev` and `prod` share a single AWS account, in eu-north-1 (spec
Revision 2).

## Consequences
- Stages are isolated by naming convention, by separate IAM roles and by separate Terraform
  state keys rather than by account boundary.
- No long-lived AWS access keys exist anywhere; deploys use the owner's short-lived `aws login`
  session, and CI holds no cloud access (ADR 0013).

## Alternatives considered
- **AWS CDK (Python):** familiar language, but it locks the project into AWS-specific tooling
  with less mature multi-cloud portability than Terraform.
- **CloudFormation:** native to AWS and free, but a weaker module reuse and testing story than
  Terraform for a project this size.
- **Multi-account stages:** the strongest isolation between stages. Not needed while only `dev`
  exists; Plan 7 revisits it when it adds `prod`.
