# 0002: Neon Postgres for relational data

- Status: Accepted; provisioning amended 2026-09-28 (Plan 3a)
- Date: 2026-09-26 (region updated 2026-09-27)

## Context
The data model needs foreign-key constraints, row-level security for tenant isolation, and
pgvector for Milestone 2. Of the AWS-native options, RDS costs about $12/month for the
smallest instance; Aurora Serverless (express) costs about $1-3/month in normal use with a
worst case around $44; Aurora DSQL is free but has no pgvector, no triggers and no
extensions, which the data model relies on.

## Decision
Use Neon's free tier, in region aws-eu-central-1 (Frankfurt), the closest Neon region to the
API in eu-north-1 (spec Revision 2, D2). Only standard Postgres features are used, so the
database stays portable to Aurora or RDS if the free tier is ever outgrown.

## Consequences
- Cost is $0. Hitting a limit pauses the database rather than generating a bill.
- The free tier caps storage at 0.5 GB, so raw uploaded files are never stored in Postgres:
  they live in S3, and only structured data (findings, evidence, analyses) goes to Neon.
- The endpoint is reachable from the public internet rather than sitting inside a VPC. This
  is an accepted risk, mitigated by mandatory TLS (`sslmode=verify-full`) and a distinct
  database role per Lambda function with its own password.
- Because only standard Postgres features are used, moving to Aurora later is a change to
  connection settings and role credentials, not a schema or query rewrite.
- **Provisioning (amended in Plan 3a):** the owner creates each stage's Neon project in the Neon
  console, which is spec §13.2's fallback. Neon's Terraform provider isn't code-signed, and the
  owner's Windows machine blocks unsigned executables, so Terraform can't run it there. The
  owner stores the owner role's connection string once (`just store-database-url`). Each deploy
  then runs the migrations and gives every function's role a generated password, stored as its
  own SSM SecureString. Tables, row-level security and grants stay in reviewed migrations.

## Alternatives considered
- **RDS:** simplest AWS-native option, but its smallest instance costs about $12/month with
  no free tier for this workload.
- **Aurora Serverless (express):** cheaper than RDS in normal use (about $1-3/month), but a
  worst case near $44/month and still no free tier.
- **Aurora DSQL:** free, but lacks pgvector, triggers and extensions the schema depends on.
- **Supabase:** free tier, but the project pauses after 7 idle days, which would break the
  demo and any scheduled jobs.
