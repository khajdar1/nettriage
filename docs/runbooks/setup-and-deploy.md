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
The AWS CLI must be 2.32 or later (for `aws login`), Terraform must be 1.11 or later, and `gh`
must be signed in.

### A2. Sign in to AWS from the command line
```bash
aws configure set region eu-north-1 --profile nettriage
aws login --profile nettriage
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
The first output line is `AWS account …, region eu-north-1`, then the account checks
(`PASS  Lambda in eu-north-1`, `PASS  IAM`, …). If any line says `FAIL`, the command stops with
`STOP: Preflight failed; nothing was created.`; send the output to Claude. Terraform then shows
the plan: the state bucket
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
database connection in SSM (after A7), the Grafana endpoint and both Lambda layers. If a layer line fails with "not found", its version
moved on. Ask Claude to update the ARN in `terraform.tfvars` from the layer's release notes.

### A7. Create the Neon database (once)
The database runs on Neon's free plan. You create the project in Neon's console; the deploy
then creates the tables and a separate login for the app. (Terraform can't manage Neon from this
machine: Neon's Terraform provider isn't code-signed, and Windows policy here blocks unsigned
programs.)
1. Sign in at **console.neon.tech** and click **New project**.
2. Fill in:
   - **Project name:** `nettriage-dev`
   - **Postgres version:** 17
   - **Cloud provider:** AWS
   - **Region:** AWS Europe Central 1 (Frankfurt)

   Leave everything else as it is and click **Create project**.
3. On the project dashboard, click **Connect**. In the dialog:
   - leave **Branch** `main`, **Database** `neondb` and **Role** `neondb_owner` as they are;
   - turn **Connection pooling** off, so the host has no `-pooler` in it;
   - click **Show password**, then **Copy snippet** (or copy the `postgresql://…` string).
4. Store it in AWS (it never goes into Git, GitHub or chat):
   ```bash
   just store-database-url dev
   ```
   Paste the string and press Enter; nothing is shown. Expected:
   `Stored /nettriage/dev/db/owner-url as a SecureString.`
5. Run `just preflight`: `PASS  Database connection in SSM` appears.

The next `just deploy-dev` runs the migrations, gives the app's role `app_api` a generated
password, and stores its connection string as `/nettriage/dev/db/app-api-url`. It prints
`Database migrated. New logins: app_api.` the first time and `Database migrated.` afterwards.

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
          { "context": "analyze (actions)" } ] } },
    { "type": "code_scanning", "parameters": { "code_scanning_tools": [
        { "tool": "CodeQL", "security_alerts_threshold": "high_or_higher", "alerts_threshold": "errors" } ] } }
  ]
}
EOF
gh api -X POST "repos/{owner}/{repo}/rulesets" --input "$TEMP/ruleset.json"
gh api "repos/{owner}/{repo}/rulesets" --jq '.[].name'
```
The last command prints `protect-main`. The `code_scanning` rule blocks merging a PR that would
introduce a new high-or-higher-severity CodeQL security alert, or a new CodeQL error, on `main`.

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
2. `just plan-dev` runs the PR's own code with your AWS session, so before running it, read the
   PR's changes to `tools/`, `justfile`, `infra/`, `.github/`, `backend/pyproject.toml` and
   `backend/uv.lock`. If anything looks wrong, or you aren't sure, don't plan it; ask Claude or
   close the PR instead.
3. While the PR's branch is checked out, plan it:
   ```bash
   git switch <pr-branch> && git pull --ff-only
   just plan-dev
   ```
   The planned changes appear in the terminal and as a PR comment titled
   `### Terraform plan: dev (<sha>)`: resource addresses only, never values. On the first
   deploy it lists the dev stage's resources (all `create`). The working tree must be clean
   (commit or stash first) and must stay on the PR's commit while the plan runs.
4. Claude runs the Copilot review loop. You read the diff and the threads, then **squash-merge**
   on GitHub. Claude never merges.
5. Sign out: `aws logout --profile nettriage`. This ends the session, so repository code you run
   later (yours or anyone else's) can't use it.

### B2. Deploy
`just deploy-dev` also runs repository code (`tools/`, `justfile`, `infra/`) with your AWS
session, but it needs no extra read here: you deploy `main`, which contains only reviewed,
merged PRs.
```bash
git switch main && git pull --ff-only
just deploy-dev
```
Wait until the `ci` and `codeql` runs for the merge commit are green on GitHub first. The
command:
1. refuses anything but a clean `main` that matches GitHub and whose CI and CodeQL passed;
2. runs the preflight;
3. downloads that commit's CI-built artifacts;
4. migrates the database and prints `Database migrated.` The first time, it also gives the
   app's database role a login and adds `New logins: app_api.`;
5. shows the Terraform plan, and you type `yes`;
6. publishes the site;
7. runs the smoke tests.

If step 2's preflight has a `FAIL` line, the command stops with
`STOP: Preflight failed; nothing was deployed.`; fix it (see Part C) and run `just deploy-dev`
again.

Success looks like eleven `PASS` lines:
- `api health`
- `api security headers`
- `web root`
- `web security headers`
- `spa route serves index.html`
- `edge rejects api call without session`
- `api 404 stays problem+json`
- `function url rejects direct calls`
- `api limits requests per viewer ip`
- `sign-in redirects to Cognito`
- `Cognito sign-in page loads`

The last line is `Deployed <sha> to dev: https://<id>.cloudfront.net`. The first deploy takes
longer, because CloudFront needs several minutes to create the distribution.

Sign out: `aws logout --profile nettriage`. This ends the session, so repository code you run
later can't use it.

### B3. Check telemetry and cost
1. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-api" && resource.deployment.environment.name = "dev" }`.
   Traces for `GET /api/health` from the smoke tests appear within a few minutes.
2. In **Explore → Prometheus**, search the metrics for `nettriage-api`; look for
   `http_server_duration_milliseconds_*` or `http_server_request_duration_seconds_*`.
3. In AWS Settings → **Billing**, the amount due is still $0.

### B4. Sign in to dev
Sign-in uses Cognito's managed login page with a password and a one-time code from an
authenticator app (TOTP). The app's own pages come in Plan 6, so for now you check the result
with the API directly.
1. Install an authenticator app on your phone: Microsoft Authenticator or Google Authenticator.
2. In your browser, open `https://<id>.cloudfront.net/api/auth/login` (the address from B2's last
   line, plus `/api/auth/login`). You land on Cognito's sign-in page.
3. Choose **Create an account**. Enter your email address and a password of at least 12
   characters, then choose **Sign up**.
4. Cognito emails you a verification code from `no-reply@verificationemail.com` (check spam).
   Enter it and confirm.
5. Set up MFA: in the authenticator app, add an account and scan the QR code on the page. Type
   the 6-digit code the app shows, give the device a name if asked, and confirm.
6. You land on `https://<id>.cloudfront.net/app`, which is still Plan 1's placeholder page.
7. Open `https://<id>.cloudfront.net/api/v1/me`. It shows your email, `"memberships": []` and a
   `csrf_token`: you're signed in, and your user exists in the database.
8. To sign in again later, repeat step 2; Cognito asks for your password and a fresh code from
   the app. A session lasts up to 12 hours, and ends after 60 minutes without activity.

Do steps 2 to 5 in one go: the API gives a sign-in 15 minutes. If it takes longer, you land on
`/?sign_in=expired`; your account is kept, so start again at step 2 and just sign in. If you land
on another `/?sign_in=...` address, see Part C.

### B5. Try organizations
The organization pages come in Plan 6. Until then, you can call the API from the browser's
developer console while signed in.
1. Open `https://<id>.cloudfront.net/api/auth/login` and sign in with your email, your password
   and a fresh code from the authenticator app. You land on `https://<id>.cloudfront.net/app`.
2. Press **F12** and choose the **Console** tab.
3. The first time you paste into the console, the browser refuses and asks you to type
   `allow pasting`. Type it and press **Enter**.
4. Paste this and press **Enter**. It defines `api(method, path, body)`, which calls the API the
   way the app will: with your CSRF token, and with the body's SHA-256 in `x-amz-content-sha256`,
   which CloudFront needs before it passes a body on to the API.
   ```js
   const me = await (await fetch("/api/v1/me")).json();
   async function api(method, path, body) {
     const headers = { "X-CSRF-Token": me.csrf_token };
     const text = body === undefined ? undefined : JSON.stringify(body);
     if (text !== undefined) {
       const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
       headers["Content-Type"] = "application/json";
       headers["x-amz-content-sha256"] = [...new Uint8Array(hash)]
         .map((byte) => byte.toString(16).padStart(2, "0"))
         .join("");
     }
     const response = await fetch("/api/v1" + path, { method, headers, body: text });
     const type = response.headers.get("content-type") || "";
     const answer = type.includes("json") ? await response.json() : await response.text();
     console.log(response.status, answer);
     return answer;
   }
   ```
5. Create an organization:
   ```js
   const org = await api("POST", "/orgs", { name: "Acme Security" });
   ```
   The console shows `201` and the org, with `slug: "acme-security"`, `role: "owner"` and
   `member_count: 1`.
6. Invite someone. Any address works; nothing is emailed yet:
   ```js
   await api("POST", "/orgs/" + org.id + "/invitations", { email: "colleague@example.com", role: "viewer" });
   ```
   `201`, with an `invite_url` ending in `/invite#` and a long token. The link is shown only this
   once. The page it opens comes in Plan 6.
7. Read the audit log:
   ```js
   await api("GET", "/orgs/" + org.id + "/audit-log");
   ```
   `200`, with `member.invited` and `org.created`, newest first.
8. Try a refusal:
   ```js
   await api("DELETE", "/orgs/" + org.id + "?confirm_name=" + encodeURIComponent("not the name"));
   ```
   `422`: deleting an org needs its exact name.
9. Delete the test org (you can have at most 3):
   ```js
   await api("DELETE", "/orgs/" + org.id + "?confirm_name=" + encodeURIComponent("Acme Security"));
   ```
   `204`.

If the console shows `401`, your session ended: sign in again and repeat from step 4.

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
| `FAIL  Database connection in SSM` | Run A7 |
| `STOP: That isn't a Postgres connection string …`, `STOP: The database must be a Neon project in AWS Europe Central 1 (Frankfurt) …` or `STOP: Use the direct connection string …` | Copy the string again as in A7 step 3, then rerun `just store-database-url dev` |
| `STOP: Can't read /nettriage/dev/db/owner-url from SSM. …` | Run A7 |
| `STOP: Database migrations failed; nothing was deployed. …` | Send the output to Claude. Nothing in AWS changed |
| `STOP: Couldn't give the database role app_api a login …` | Check the stored string (A7), then send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
| `FAIL  Lambda Web Adapter layer` or `FAIL  OpenTelemetry collector layer` … `isn't a eu-north-1 layer ARN; fix it in terraform.tfvars` or `not found or not shared; check the layer's current version…` | Ask Claude to update the layer ARN to its current version |
| `STOP: Couldn't comment on this branch's PR: …` | Open the branch's PR, then plan again. To plan without posting a comment, run `uv run --project backend python -m tools.deploy plan --no-comment` (`just plan-dev` always posts) |
| `STOP: Deploys run from main …`, `… uncommitted changes …` or `… differs from GitHub's main …` | Follow the command in the message. `… uncommitted changes …` also applies to `just plan-dev` |
| `STOP: No ci.yml or codeql.yml run found for <sha>…` | Push the branch (if you haven't already), wait for the workflow(s) to run for it, then plan or deploy again. `just plan-dev` only checks `ci.yml`; `just deploy-dev` checks both |
| `STOP: ci.yml or codeql.yml for <sha> is still in_progress…` or `concluded 'failure'…` | Wait for CI, or fix it; only green commits are planned or deployed. Applies to `just plan-dev` (`ci.yml` only) and `just deploy-dev` (both) |
| `STOP: The checkout changed during the deploy …` or `… during the plan …` | Something changed the branch or the tree while the command was checking CI and downloading artifacts, and it stopped before Terraform ran. Check the tree, then run the command again |
| `STOP: Couldn't download … CI artifacts expire after 7 days …` | On GitHub, re-run the `ci` workflow for that commit, then deploy again |
| `STOP: Smoke tests failed …` | Read the `FAIL` lines. Send them to Claude, or roll back |
| `FAIL  Cognito sign-in page loads` right after the first Plan 3b deploy | A new Cognito domain can take a few minutes to start answering. Wait 5 minutes, then run `just deploy-dev` again (Terraform has nothing left to change). If it still fails, send the output to Claude |
| `FAIL  sign-in redirects to Cognito` or `FAIL  api limits requests per viewer ip` | Send the output to Claude. Sign-in, or rate limiting by IP, isn't working on the deployed stage |
| The browser lands on `/?sign_in=expired` | The sign-in took longer than 15 minutes, was finished in a different browser from the one that started it, or a page was reloaded or opened twice. Start again from `/api/auth/login` |
| The browser lands on `/?sign_in=failed` | The sign-in was cancelled, or Cognito's answer was refused. Start again; if it keeps happening, send Claude the time it happened (the logs record why, as `sign_in_failed`) |
| The browser lands on `/?sign_in=unavailable` | DynamoDB, Neon or Cognito didn't answer. Wait a minute and start again; if it keeps happening, tell Claude |
| The browser lands on `/?sign_in=limited` | Too many sign-ins from your network in a short time. Wait a minute, then start again |
| The browser lands on `/?sign_in=disabled` | This account is disabled in the database. Tell Claude if that's unexpected |
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account. Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| Cognito's verification email never arrives | Check spam. Cognito's built-in sender allows about 50 emails a day per account; wait until tomorrow if many sign-ups ran today |
| `Error acquiring the state lock` | Another plan or deploy is running, or one was interrupted. Wait a minute and retry; if it persists, send the lock ID to Claude |
| `` STOP: `terraform apply` failed with exit code 1. `` | Terraform's own error is printed above this line (`apply` shares the terminal), so scroll up and read it. If it's `Error acquiring the state lock`, see that row; otherwise send the output to Claude. Terraform may have made some changes before failing; the next plan or deploy shows what's left |
| `` STOP: `terraform init` failed with exit code 1: Error: … `` (or any other `` `<tool> <command>` failed … ``) | Read the `Error:` text. `Error acquiring the state lock` is covered by its own row; for anything else, send the output to Claude |
| Terraform's own `Failed to persist state to backend` (an `errored.tfstate` file appears in `infra/envs/dev/` or `infra/bootstrap/`) | Don't commit, share or open `errored.tfstate` in a chat: it holds secrets from the state. Tell Claude the message, not the file; Claude helps you run `terraform state push errored.tfstate` from that directory, then delete it. (`git check-ignore -v infra/envs/dev/errored.tfstate` confirms it's git-ignored, matched by the repo's `*.tfstate` pattern.) |
| `` STOP: `<tool>` isn't installed or isn't on PATH. `` | Install it (A1 lists the tools) |
| `STOP: The first bootstrap didn't finish; its state is saved in …terraform.tfstate.recovered (git-ignored) …` | Keep `infra/bootstrap/terraform.tfstate.recovered`. Don't run `just bootstrap` again; send the output to Claude |
| `STOP: A saved bootstrap state exists at … Don't bootstrap again …` | A previous first bootstrap didn't finish. Send the output to Claude, who pushes the saved state into the state bucket with you |

### The Free plan deadline
The free plan ends on **2027-03-08**. Before then, decide whether to upgrade: that costs about
$0–1 a month, and the $120 of credits are forfeited. The alternative is to let the account close:
AWS keeps the data for 90 days. Plan 7 adds a `just destroy-dev` command for tearing the stage
down cleanly.
