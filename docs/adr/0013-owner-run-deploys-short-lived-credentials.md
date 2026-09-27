# 0013: Owner-run deploys with short-lived credentials; CI holds no cloud access

- Status: Accepted
- Date: 2026-09-27

## Context
The AWS account was created with AWS's newer sign-up experience, which places it in an
AWS-managed organization (spec Revision 2). Its policies deny creating IAM identity providers
on both the Free and the Paid plan, so GitHub Actions can't sign in to the account with OIDC,
which ADR 0011 relied on. Lifting that restriction is irreversible ("Activate advanced
features") or forfeits the account's $120 of Free plan credits. The one option left inside the
rules, an IAM user whose access key is stored in GitHub, would put a long-lived key in CI.

## Decision
Split CI from CD.
- GitHub Actions runs every check and builds the deployable artifacts once (the Lambda zip and
  the web build), with no AWS access of any kind. `tools/check_no_cloud_access.py` fails CI if
  a workflow asks for an OIDC token, uses an AWS action or references AWS credentials, or if
  Terraform defines a GitHub OIDC trust.
- The owner deploys from their machine with a short-lived `aws login` session. `just deploy-dev`
  accepts only a clean `main` checkout equal to GitHub's `main` whose `ci` and `codeql` runs
  succeeded, runs the preflight checks, downloads that commit's CI artifacts, applies Terraform
  (the owner confirms the plan), publishes the site and runs the smoke tests.
- `just plan-dev` plans only a commit whose `ci` run succeeded, with its CI artifacts, and posts
  the planned changes, addresses only, to the PR.
- Deploy secrets live in SSM Parameter Store as SecureStrings and are read at deploy time.

## Consequences
- No long-lived credentials exist anywhere: not in GitHub, not as IAM users or access keys. The
  `aws login` session is cached on the owner's machine only for its own short lifetime. A
  compromised workflow or dependency in CI can't reach AWS at all.
- Commands reach that session through a helper AWS profile, `nettriage-tools`, whose
  `credential_process` asks the AWS CLI for the current `aws login` session. The tool creates
  this profile on first use. It stores no credentials of its own, and it keeps long Terraform
  runs working past the session's 15-minute credentials.
- What's deployed is exactly what CI built and tested, and only after CI and CodeQL passed for
  that commit.
- Deploys are deliberate owner actions; nothing deploys on merge. They depend on the owner's
  machine and sign-in, and CI artifacts expire after 7 days (re-running CI refreshes them).
- Local plans and deploys run the repository's own code with the owner's session. So the owner
  reviews a PR's changes to the deploy tooling, infrastructure, workflows and lockfiles before
  `just plan-dev`, and signs out when done; CI's lack of cloud access doesn't cover this step.
- Unattended jobs that need AWS (nightly evals, backups) can't run in GitHub Actions. They run
  inside AWS as scheduled Lambdas or as owner-run commands (Plans 5 and 7).
- The bootstrap no longer creates an OIDC provider, CI roles or a permissions boundary.

## Alternatives considered
- **GitHub OIDC (ADR 0011's original design):** blocked by the account's policies.
- **Activate advanced features:** unlocks OIDC and us-east-1, but it's irreversible and needs
  the Paid plan, which forfeits the credits.
- **A new standalone AWS account:** existing AWS customers get no Free plan credits.
- **An IAM user's access key in GitHub secrets:** a long-lived key in CI.
- **A self-hosted GitHub runner on the owner's machine:** any PR's jobs would run next to the
  owner's credentials.
