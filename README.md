# NetTriage

AI-assisted triage of network threats. Upload AWS VPC Flow Logs, get detections mapped to
MITRE ATT&CK, and a checked, plain-English explanation for every finding.

> Status: Milestone 1 in progress. Deployed so far: the walking skeleton (CI, owner-run
> deploys, infrastructure as code, telemetry), the detection engine, the Postgres data
> foundation, sign-in with mandatory MFA, organizations, uploads and their analysis, triage,
> and AI explanations on Bedrock. In progress: the web app, whose pages for organizations,
> uploads, findings, triage, the AI's explanations, the audit log and usage are built, with
> a home of your work and each organization's findings at a glance; the public demo is next.
> Operations has begun: nightly backups checked by a weekly restore drill, a daily cleanup,
> and probes.

## Architecture

```mermaid
flowchart LR
  B["Browser: React SPA"] -->|HTTPS| CF["CloudFront<br/>TLS, security headers,<br/>edge session check"]
  CF -->|"/*"| WEB[("S3: SPA")]
  CF -->|"/api/* (SigV4 via OAC)"| API["Lambda: FastAPI"]
  API -.OTLP.-> GC["Grafana Cloud"]
```

The full design, including what later milestones add, is in the
[spec](docs/superpowers/specs/2026-09-26-nettriage-m1-design.md). Every major choice has an
[architecture decision record](docs/adr/).

Regional resources run in eu-north-1 (Stockholm); CloudFront serves the app worldwide.

## Highlights so far

- About **$0 a month**: serverless on AWS Always Free, with budgets and anomaly alerts ([ADR 0001](docs/adr/0001-serverless-on-aws-always-free.md)).
- **No stored cloud credentials**: CI checks and builds with no AWS access; the owner deploys CI-built, CI-green commits with a short-lived sign-in ([ADR 0013](docs/adr/0013-owner-run-deploys-short-lived-credentials.md)). The Lambda Function URL only accepts CloudFront-signed requests ([ADR 0003](docs/adr/0003-cloudfront-to-function-url-with-oac.md)).
- **Strict security headers** from the edge, including a CSP with no inline scripts (checked in CI).
- **OpenTelemetry** traces and metrics in Grafana Cloud; RFC 9457 errors that carry the trace ID.
- **Sign-in with mandatory TOTP MFA** through Cognito, the backend-for-frontend way: the browser only holds an opaque, HttpOnly session cookie, with CSRF checks on every state-changing request ([ADR 0004](docs/adr/0004-backend-for-frontend-sessions.md)).
- **Organizations with roles** (owner, admin, analyst, viewer) and one-time invitation links; a test calls every endpoint as each role, a non-member and an anonymous caller.
- **Direct-to-S3 uploads**: the API hands out a presigned PUT that signs the file's size and SHA-256, so S3 accepts only the declared file and the API never handles it.
- **Event-driven analysis**: each upload queues a worker Lambda that streams and parses the file within size, row and decompression limits, runs three detectors, and stores each finding exactly once with its evidence and MITRE ATT&CK techniques, even when a message arrives twice.
- **Triage with optimistic concurrency**: a finding's status and assignee change only with `If-Match` naming the version the caller read, so two analysts can't silently overwrite each other (412 instead); every change and comment is in the finding's history and the audit log.
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference. Files go from the browser straight to S3 with their fingerprint and progress; findings open most severe first; a port scan's evidence is drawn on a map of the host's ports; and triage names the version it read, so a newer change is shown, never overwritten. The home lists what's assigned to you across organizations, and every count on it, or above an organization's findings, links to exactly the findings it counts.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
- **Backups known to work**: every night a Lambda dumps the database from a consistent snapshot to S3, and every week it restores the newest dump into a throwaway Postgres inside the function and checks every table's rows against the backup's manifest. Its Postgres is built from source in CI and proven inside AWS's own Lambda image, so CI still needs no cloud access. No role, the backup's included, skips row-level security.
- **Distributed rate limiting** with GCRA on DynamoDB, exact under concurrency ([ADR 0006](docs/adr/0006-gcra-rate-limiter.md)).
- **Supply chain**: SHA-pinned actions, CodeQL, dependency review, Dependabot, Checkov and tflint.

## Develop

Prerequisites: uv, Node LTS with pnpm, Terraform, just (see the plan's prerequisites). Deploys
also need AWS CLI 2.32 or later, Terraform 1.11 or later and a signed-in GitHub CLI (`gh`).

```bash
just             # list tasks
just lint test   # backend checks
just web-check   # frontend lint, tests, build and CSP check
just tf-check    # Terraform format, validate and tests
just cloud-check # CI holds no cloud access
```

Backend tests start a local Postgres on their own (no Docker needed); `just db-down` stops it.

Detector quality: `just detection-report` scores every detector's precision and recall on a
seeded, synthetic scenario suite (target: 0.90 or more for both). CI publishes the report on
every run as the `detection-report` artifact and in the job summary.

## Deploy

Only the owner deploys, from their machine, with a short-lived AWS sign-in. The step-by-step
guide is [docs/runbooks/setup-and-deploy.md](docs/runbooks/setup-and-deploy.md).

```bash
aws login --profile nettriage   # short-lived session
just preflight                  # read-only checks of the account
just plan-dev                   # on a PR: plan and post the changes
just deploy-dev                 # on main: deploy CI's artifacts, then smoke tests
just restore-drill-dev          # restore the newest backup and check its counts
```

## License

Apache-2.0, except the ATT&CK data in
`backend/src/nettriage/reference/attack_techniques.json`: 12 techniques from MITRE ATT&CK® 19.2,
which the API returns with MITRE's notice and license.

Copyright 2015-2026, The MITRE Corporation. MITRE ATT&CK and ATT&CK are registered trademarks of
The MITRE Corporation. The MITRE Corporation (MITRE) hereby grants you a non-exclusive,
royalty-free license to use ATT&CK® for research, development, and commercial purposes. Any copy
you make for such purposes is authorized provided that you reproduce MITRE's copyright designation
and this license in any such copy. © 2026 The MITRE Corporation. This work is reproduced and
distributed with the permission of The MITRE Corporation.
