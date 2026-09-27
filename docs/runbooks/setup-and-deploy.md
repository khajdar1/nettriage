# Setup and deploy (owner runbook)

Only the owner runs these steps, from their own machine. They cost $0: the AWS account is on
the Free plan until 2027-03-08 and can't be charged (spec Revision 2). Commands are for **Git
Bash in Windows Terminal**, from `C:\dev\nettriage`. Password and token prompts need a real
terminal, not an embedded one.

## Part A: one-time setup

### A1. Check the tools
```bash
aws --version; terraform version; gh auth status; just --version; uv --version
```
The AWS CLI must be 2.32 or later (for `aws login`), and `gh` must be signed in.

### A2. Sign in to AWS from the command line
```bash
aws login --profile nettriage
aws configure set region eu-north-1 --profile nettriage
aws sts get-caller-identity --profile nettriage
```
`aws login` opens your browser; sign in as you do on the AWS console. The last command prints
your account ID. The session is short-lived: when a command later says
`STOP: No usable AWS session …`, run `aws login --profile nettriage` again. Never create access
keys; the deploy tool refuses them.

The first `just` command that uses AWS (A3's `just store-grafana-token`) creates a second AWS
profile, `nettriage-tools`, and prints
`Created AWS profile 'nettriage-tools', which refreshes your 'nettriage' session for long commands.`
It only refreshes your `aws login` session during long Terraform runs; don't edit or delete it.

### A3. Create the Grafana Cloud stack
1. Create a free stack at grafana.com.
2. Open **Connections → OpenTelemetry (OTLP)** and note three values:
   - the **OTLP endpoint** (it starts with `https://`);
   - the **instance ID** (a number);
   - a new **token** with the `metrics:write`, `logs:write` and `traces:write` scopes.
3. Put the endpoint in `infra/envs/dev/terraform.tfvars` (`grafana_otlp_endpoint = "…"`) through
   a PR, or send it to Claude to add. It isn't a secret.
4. Store the token in AWS (it never goes into Git or GitHub):
   ```bash
   just store-grafana-token dev
   ```
   Enter the instance ID, then paste the token; the token isn't shown. If the token prompt
   doesn't appear or echoes the token, you aren't in a real console: open Windows Terminal and
   run it there. (getpass needs a real console.) Expected:
   `Stored /nettriage/dev/grafana-otlp-auth as a SecureString.`

### A4. Bootstrap the account (once)
Optional: find the account's cost anomaly monitor, so anomaly alerts also email you:
```bash
aws ce get-anomaly-monitors --profile nettriage --region us-east-1 --query 'AnomalyMonitors[].[MonitorName,MonitorArn]' --output table
```
Then run one of these (use your email; add the monitor ARN if one was listed):
```bash
just bootstrap you@example.com
just bootstrap you@example.com arn:aws:ce::123456789012:anomalymonitor/…
```
The first lines are the account checks (`PASS  Lambda in eu-north-1`, `PASS  IAM`, …). If any
line says `FAIL`, the command stops with `STOP: Preflight failed; nothing was created.`; send the
output to Claude. Terraform then shows the plan: the state bucket
`nettriage-tfstate-<account>` with its settings, the `nettriage-monthly` budget and, optionally,
the anomaly subscription. Type `yes`. If the command stops partway, it keeps the partial state in
`infra/bootstrap/terraform.tfstate.recovered`; keep that file (see Part C). It ends with
`Bootstrap state is now in s3://nettriage-tfstate-<account>/bootstrap/terraform.tfstate`.
Confirm the AWS Budgets email if one arrives.

### A5. Run the preflight
```bash
just preflight
```
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
Grafana endpoint and both Lambda layers. If a layer line fails with "not found", its version
moved on. Ask Claude to update the ARN in `terraform.tfvars` from the layer's release notes.

### A6. Set up the GitHub repository (once)
```bash
gh repo edit --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false --delete-branch-on-merge
gh api -X PUT "repos/{owner}/{repo}/vulnerability-alerts"
gh api -X PUT "repos/{owner}/{repo}/automated-security-fixes"
gh api -X PUT "repos/{owner}/{repo}/private-vulnerability-reporting"
echo '{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}' \
  | gh api -X PATCH "repos/{owner}/{repo}" --input -
```
If the PR's `dependency-review` check still says "Dependency graph is not enabled", turn on
**Dependency graph** at https://github.com/khajdar1/nettriage/settings/security_analysis.

Protect `main`. Approvals are 0 because you can't approve your own PR; required checks,
resolved threads and linear history still apply:
```bash
cat > "$TEMP/ruleset.json" <<'EOF'
{
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "backend" }, { "context": "package" }, { "context": "frontend" },
          { "context": "edge-functions" }, { "context": "terraform" },
          { "context": "analyze (python)" }, { "context": "analyze (javascript-typescript)" },
          { "context": "analyze (actions)" } ] } }
  ]
}
EOF
gh api -X POST "repos/{owner}/{repo}/rulesets" --input "$TEMP/ruleset.json"
gh api "repos/{owner}/{repo}/rulesets" --jq '.[].name'
```
The last command prints `protect-main`.

Check Copilot code review at github.com/settings/copilot (choose **Lite** effort if the setting
exists). Then connect the GitHub MCP server for Claude's review loop:
1. Create a fine-grained token at github.com/settings/personal-access-tokens/new.
   - Repository access: **only `nettriage`**.
   - Permissions: **Pull requests: Read and write** and **Contents: Read-only**.
   - Expiration: 90 days.
2. In a terminal (not inside Claude), run:
   ```bash
   claude mcp add-json github '{"type":"http","url":"https://api.githubcopilot.com/mcp/x/pull_requests","headers":{"Authorization":"Bearer <token>"}}'
   claude mcp list
   ```
   `github` shows as connected. If `request_copilot_review` is missing from `/mcp` in Claude,
   also add `https://api.githubcopilot.com/mcp/x/copilot` the same way under the name
   `github-copilot`.

GitHub needs no deployment environments, variables or secrets for NetTriage.

## Part B: every change

### B1. Review
1. Claude opens the PR, and CI must go green.
2. While the PR's branch is checked out, plan it:
   ```bash
   git switch <pr-branch> && git pull --ff-only
   just plan-dev
   ```
   The planned changes appear in the terminal and as a PR comment titled
   `### Terraform plan: dev (<sha>)`: resource addresses only, never values. On the first
   deploy it lists the dev stage's resources (all `create`).
3. Claude runs the Copilot review loop. You read the diff and the threads, then **squash-merge**
   on GitHub. Claude never merges.

### B2. Deploy
```bash
git switch main && git pull --ff-only
just deploy-dev
```
Wait until the `ci` and `codeql` runs for the merge commit are green on GitHub first. The
command:
1. runs the preflight;
2. refuses anything but a clean `main` that matches GitHub and whose CI and CodeQL passed;
3. downloads that commit's CI-built artifacts;
4. shows the Terraform plan, and you type `yes`;
5. publishes the site;
6. runs the smoke tests.

If step 1's preflight has a `FAIL` line, the command stops with
`STOP: Preflight failed; nothing was deployed.`; fix it (see Part C) and run `just deploy-dev`
again.

Success looks like eight `PASS` lines:
- `api health`
- `api security headers`
- `web root`
- `web security headers`
- `spa route serves index.html`
- `edge rejects api call without session`
- `api 404 stays problem+json`
- `function url rejects direct calls`

The last line is `Deployed <sha> to dev: https://<id>.cloudfront.net`. The first deploy takes
longer, because CloudFront needs several minutes to create the distribution.

### B3. Check telemetry and cost
1. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-api" && resource.deployment.environment.name = "dev" }`.
   Traces for `GET /api/health` from the smoke tests appear within a few minutes.
2. In **Explore → Prometheus**, search the metrics for `nettriage-api`; look for
   `http_server_duration_milliseconds_*` or `http_server_request_duration_seconds_*`.
3. In AWS Settings → **Billing**, the amount due is still $0.

## Part C: when things go wrong

### Roll back
On GitHub, open the merged PR and choose **Revert**, which opens a revert PR. Merge it after CI
passes, then run B2 again.

### Messages and fixes
| Message | What to do |
|---|---|
| `STOP: No usable AWS session …` | `aws login --profile nettriage` |
| `STOP: Profile 'nettriage' uses long-lived access keys …` | Delete the keys from `~/.aws/credentials`, then `aws login --profile nettriage` |
| `FAIL  Lambda in eu-north-1`, `FAIL  CloudFront` or another account check (`AccessDenied`, "explicit deny") | AWS changed the account's policies. Don't retry; send the output to Claude |
| `FAIL  Terraform state bucket` | Run A4 (bootstrap) |
| `FAIL  Grafana token in SSM` | Run `just store-grafana-token dev` |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
| `FAIL  Lambda Web Adapter layer` or `FAIL  OpenTelemetry collector layer` … `isn't a eu-north-1 layer ARN; fix it in terraform.tfvars` or `not found or not shared` | Ask Claude to update the layer ARN to its current version |
| `STOP: CI hasn't finished for <sha> …` | Push the branch, wait for the `ci` workflow, then plan again |
| `STOP: Couldn't comment on this branch's PR: …` | Open the branch's PR, then plan again. To plan without posting a comment, run `uv run --project backend python -m tools.deploy plan --no-comment` (`just plan-dev` always posts) |
| `STOP: Deploys run from main …`, `… uncommitted changes …` or `… differs from GitHub's main …` | Follow the command in the message |
| `STOP: ci.yml for <sha> is still in_progress` or `concluded 'failure'` | Wait for CI, or fix it; only green commits deploy |
| `STOP: Couldn't download … CI artifacts expire after 7 days …` | On GitHub, re-run the `ci` workflow for that commit, then deploy again |
| `STOP: Smoke tests failed …` | Read the `FAIL` lines. Send them to Claude, or roll back |
| `Error acquiring the state lock` | Another plan or deploy is running, or one was interrupted. Wait a minute and retry; if it persists, send the lock ID to Claude |
| `` STOP: `terraform apply` failed with exit code 1. `` | Terraform's own error is printed above this line (`apply` shares the terminal), so scroll up and read it. If it's `Error acquiring the state lock`, see that row; otherwise send the output to Claude. Terraform may have made some changes before failing; the next plan or deploy shows what's left |
| `` STOP: `terraform init` failed with exit code 1: Error: … `` (or any other `` `<tool> <command>` failed … ``) | Read the `Error:` text. `Error acquiring the state lock` is covered by its own row; for anything else, send the output to Claude |
| `` STOP: `<tool>` isn't installed or isn't on PATH. `` | Install it (A1 lists the tools) |
| `STOP: The first bootstrap didn't finish; its state is saved in …terraform.tfstate.recovered (git-ignored) …` | Keep `infra/bootstrap/terraform.tfstate.recovered`. Don't run `just bootstrap` again; send the output to Claude |
| `STOP: A saved bootstrap state exists at … Don't bootstrap again …` | A previous first bootstrap didn't finish. Send the output to Claude, who pushes the saved state into the state bucket with you |

### The Free plan deadline
The free plan ends on **2027-03-08**. Before then, decide whether to upgrade: that costs about
$0–1 a month, and the $120 of credits are forfeited. The alternative is to let the account close:
AWS keeps the data for 90 days. Plan 7 adds a `just destroy-dev` command for tearing the stage
down cleanly.
