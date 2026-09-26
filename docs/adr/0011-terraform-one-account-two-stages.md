# 0011: Terraform, one account, two stages

- Status: Accepted
- Date: 2026-09-26

## Context
The project needs repeatable, reviewable infrastructure across a `dev` and a `prod` stage,
state locking safe for concurrent applies, and CI that can deploy without holding long-lived
AWS credentials. Using AWS Organizations to separate stages into different accounts would
mean forfeiting this account's Free Tier credits.

## Decision
Terraform 1.11 or later, with native S3 state locking (`use_lockfile = true`, no DynamoDB
lock table). The owner applies a one-time bootstrap stack by hand; everything after that
changes only through CI. `dev` and `prod` share a single AWS account. CI authenticates to AWS
through GitHub OIDC, assuming short-lived roles scoped per stage and per job.

## Consequences
- Staying in one account means no AWS Organizations and no forfeited Free Tier credits, so
  stages are isolated by naming convention, by separate IAM roles, and by separate Terraform
  state keys rather than by account boundary.
- No long-lived AWS access keys exist anywhere in the project; the CI jobs that touch AWS
  (the PR plan job and the dev deploy job) assume a role through GitHub OIDC for the
  duration of that job.
- Because a deploy role in a single account can, in principle, create a new IAM role and
  attach it broader permissions than its own, roles that CI deploys create must carry a
  stage permissions boundary (`nettriage-dev-boundary`), so a deploy role can never create a
  role more privileged than itself.

## Alternatives considered
- **AWS CDK (Python):** familiar language, but locks the project into AWS-specific tooling
  with less mature multi-cloud portability than Terraform.
- **CloudFormation:** native to AWS and free, but weaker module reuse and testing story than
  Terraform for a project this size.
- **Multi-account through AWS Organizations:** the strongest isolation between stages, but it
  forfeits this account's Free Tier credits, which the project's cost budget depends on.
