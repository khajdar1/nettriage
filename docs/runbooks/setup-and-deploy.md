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
`nettriage-tfstate-<account>` with its settings, the `nettriage-monthly` budget, the
`nettriage-deny-bedrock` policy and, optionally, the anomaly subscription. Type `yes`. The $5
Bedrock cutoff itself waits for A8, because it needs a stage's triage worker. If the command stops partway, it keeps the partial state in
`infra/bootstrap/terraform.tfstate.recovered`; keep that file (see Part C). It ends with
`Bootstrap state is now in s3://nettriage-tfstate-<account>/bootstrap/terraform.tfstate`.
Confirm the AWS Budgets email if one arrives.

### A8. Turn on the $5 Bedrock cutoff (once, after Plan 5b's deploy)
Plan 5b adds two things to the bootstrap (spec §6.7):
- the monthly budget counts usage before credits. With credits counted, the Free plan's usage nets
  to $0, so no alert would ever fire;
- at $5 of usage in a month, a Budgets action denies Bedrock to the stages' triage workers. The
  bootstrap finds their roles by name (`nettriage-<stage>-triage`) and creates the action only
  when one exists, so run this after B2 has deployed Plan 5b.

1. `aws login --profile nettriage` (skip this if you're signed in).
2. On `main`, run the bootstrap again with the same email, and the anomaly monitor ARN if you used
   one in A4:
   ```bash
   just bootstrap you@example.com
   ```
3. Terraform shows the plan. Expect `Plan: 4 to add, 1 to change, 0 to destroy`: the
   `nettriage-deny-bedrock` policy, the `nettriage-budget-action` role and its policy, the budget
   action, and the budget's new credit setting. (On an account first bootstrapped after Plan 5b,
   the policy and the credit setting exist already: `3 to add, 0 to change`.)
   - If the plan says `1 to destroy` (the anomaly subscription), type `no`, then run step 2 again
     with your anomaly monitor ARN (A4 shows how to find it).
   - Otherwise type `yes`.
4. In the AWS console, open **Billing and Cost Management → Budgets → nettriage-monthly**. The
   **Actions** tab lists one action at $5.00 that applies the IAM policy `nettriage-deny-bedrock`,
   with the status **Standby** (waiting for its threshold). Any other status means Budgets didn't
   accept it: send Claude what it says.

### A5. Run the preflight
```bash
just preflight
```
Every line must say `PASS`, including the Terraform state bucket, the Grafana token in SSM, the
database connection in SSM (after A7), the Grafana endpoint, both Lambda layers and the triage model on Bedrock. If a layer line fails with "not found", its version
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
4. migrates the database and prints `Database migrated.`, then
   `Reference data synced: 3 detectors, 12 ATT&CK techniques.` When a function's database role
   is new, it also gives it a login and adds `New logins: <role>.` to the first line (Plan 4b
   added `app_analyze`, Plan 5b adds `app_triage`);
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

### B6. Try an upload
Files go from the browser straight to S3, with a presigned PUT the API hands out. This checks
that S3 refuses any file other than the one the API signed for (spec §13.2). The file here isn't
a real flow log, so the worker marks the upload `failed` a few seconds after step 6; B7 uploads
one that is.
1. Do B5 steps 1 to 4 (sign in, open the console, define `api`).
2. Create an org to upload into:
   ```js
   const org = await api("POST", "/orgs", { name: "Upload Test" });
   ```
3. Prepare a small file and its SHA-256:
   ```js
   const file = new TextEncoder().encode("version srcaddr dstaddr srcport dstport protocol packets bytes start end action\n");
   const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
   const sha256 = hex(await crypto.subtle.digest("SHA-256", file));
   ```
4. Ask to upload it:
   ```js
   const created = await api("POST", "/orgs/" + org.id + "/uploads", { filename: "test.log", size_bytes: file.length, sha256 });
   ```
   `201`, with `upload.status: "pending_upload"`, an `upload_url` on
   `nettriage-dev-uploads-….s3.eu-north-1.amazonaws.com` and two `upload_headers` to send with
   the PUT: `x-amz-checksum-sha256` and `x-amz-meta-traceparent`. The browser adds the file's
   length itself. The URL works for 5 minutes, so do steps 5 and 6 right away.
5. Check that S3 refuses a different file. Same length, different content:
   ```js
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: new TextEncoder().encode("VERSION srcaddr dstaddr srcport dstport protocol packets bytes start end action\n") })).status;
   ```
   `400` (the checksum doesn't match). One byte longer:
   ```js
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: new TextEncoder().encode("version srcaddr dstaddr srcport dstport protocol packets bytes start end action\n\n") })).status;
   ```
   `403` (the signed length doesn't match). If the console shows `TypeError: Failed to fetch`
   instead of a number, S3 refused the file too: open the **Network** tab to see the PUT's
   `400` or `403`.
6. Upload the real file:
   ```js
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: file })).status;
   ```
   `200`.
7. List the org's uploads:
   ```js
   await api("GET", "/orgs/" + org.id + "/uploads");
   ```
   `200`, with your upload. It shows `pending_upload` for a few seconds, then
   `status: "failed"` with `failure_reason: "the file contains no flow records"`: the file only has
   a header line.
8. Delete the test org when you're done (its file is deleted from S3 after 30 days):
   ```js
   await api("DELETE", "/orgs/" + org.id + "?confirm_name=" + encodeURIComponent("Upload Test"));
   ```

If step 5 gives `200`, S3 accepted a file it shouldn't have: stop and tell Claude (spec §13.2's
fallback is a presigned POST).

### B7. Try an analysis
An upload is analyzed by the `analyze` worker as soon as S3 has it: the bucket notifies a queue,
and the worker parses the file, runs the detectors and stores their findings. The findings pages
come in Plan 6; until then you read them from the API.
1. Do B5 steps 1 to 4 (sign in, open the console, define `api`).
2. Create an org:
   ```js
   const org = await api("POST", "/orgs", { name: "Analysis Test" });
   ```
3. Make a small flow log: an outside address (`203.0.113.9`, reserved for examples) probing 150
   ports on one internal host within a minute, an hour ago, all rejected. Then its SHA-256:
   ```js
   const start = Math.floor(Date.now() / 1000) - 3600;
   const lines = [];
   for (let port = 1; port <= 150; port++) lines.push(`2 123456789012 eni-1 203.0.113.9 10.0.0.5 40000 ${port} 6 1 40 ${start + (port % 60)} ${start + (port % 60)} REJECT OK`);
   const file = new TextEncoder().encode(lines.join("\n") + "\n");
   const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
   const sha256 = hex(await crypto.subtle.digest("SHA-256", file));
   ```
4. Upload it:
   ```js
   const created = await api("POST", "/orgs/" + org.id + "/uploads", { filename: "scan.log", size_bytes: file.length, sha256 });
   (await fetch(created.upload_url, { method: "PUT", headers: created.upload_headers, body: file })).status;
   ```
   `201`, then `200`.
5. Wait 20 seconds (the worker's first run starts cold), then read the upload:
   ```js
   await api("GET", "/orgs/" + org.id + "/uploads/" + created.upload.id);
   ```
   `200`, with `status: "analyzed"`, `rows_parsed: 150`, `rows_rejected: 0`, and `flow_start`
   and `flow_end` an hour ago. If it still says `pending_upload` or `processing`, wait a minute
   and read it again; if it hasn't changed, see "An upload isn't analyzed" in Part C.
6. List the org's findings:
   ```js
   const list = await api("GET", "/orgs/" + org.id + "/findings");
   ```
   `200`, with one finding: `detector_id: "port_scan"`, `severity: "medium"`, `status: "open"`
   and the title `Port scan of 10.0.0.5 from 203.0.113.9: 150 TCP ports in 5 minutes`.
7. Read it:
   ```js
   await api("GET", "/orgs/" + org.id + "/findings/" + list.findings[0].id);
   ```
   `200`, with 50 `evidence` flows (the first 10, the last 10 and 30 between them),
   `techniques` T1595 "Active Scanning" and T1595.001 "Scanning IP Blocks", and one `created`
   event.
8. Read a technique:
   ```js
   await api("GET", "/attack-techniques/T1595");
   ```
   `200`, with `tactics: ["reconnaissance"]`, `attack_version: "19.2"`, and MITRE's `notice` and `license`.
9. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-analyze" && resource.deployment.environment.name = "dev" }`.
   Within a few minutes there is an `analyze.upload` trace, with `analyze.parse` and
   `analyze.detect` inside it and a link to the upload request's trace.
10. Delete the test org, which deletes its uploads and findings (the file itself is deleted from
    S3 after 30 days):
    ```js
    await api("DELETE", "/orgs/" + org.id + "?confirm_name=" + encodeURIComponent("Analysis Test"));
    ```
    `204`.

### B8. Try triage
A finding's status and assignee change only with `If-Match`: the `ETag` of the version you read.
If someone changed the finding since, the API refuses with `412` instead of overwriting their
change.
1. Do B7 steps 1 to 6 (sign in, define `api`, upload the port scan and list its finding). Don't
   delete the org yet.
2. Keep the finding's address, and define `triage(body, etag)`, which sends a change with an
   optional `If-Match`:
   ```js
   const f = "/orgs/" + org.id + "/findings/" + list.findings[0].id;
   async function triage(body, etag) {
     const text = JSON.stringify(body);
     const headers = { "X-CSRF-Token": me.csrf_token, "Content-Type": "application/json", "x-amz-content-sha256": hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))) };
     if (etag) headers["If-Match"] = etag;
     const response = await fetch("/api/v1" + f, { method: "PATCH", headers, body: text });
     console.log(response.status, response.headers.get("etag"), await response.json());
   }
   ```
3. Change the status without `If-Match`:
   ```js
   await triage({ status: "investigating" });
   ```
   `428`, "Send If-Match with the ETag of the finding you read".
4. Change it with the ETag you read (`"1"`, a finding's first version):
   ```js
   await triage({ status: "investigating" }, '"1"');
   ```
   `200`, the new ETag `"2"`, and the finding with `status: "investigating"`.
5. Send the same old ETag again, as a second person who read version 1 would:
   ```js
   await triage({ status: "false_positive" }, '"1"');
   ```
   `412`, the current ETag `"2"`, and "This finding changed since you read it. Reload it and try
   again." The status stays `investigating`.
6. Assign the finding to yourself:
   ```js
   await triage({ assignee_id: me.user.id }, '"2"');
   ```
   `200`, ETag `"3"`, with your ID as `assignee_id`.
7. Comment on it:
   ```js
   await api("POST", f + "/comments", { text: "Our weekly scanner. Safe to close." });
   ```
   `201`, with `type: "commented"` and your text.
8. Read its history:
   ```js
   (await api("GET", f)).events.map((event) => event.type);
   ```
   `["created", "status_changed", "assigned", "commented"]`.
9. Read the audit log:
   ```js
   await api("GET", "/orgs/" + org.id + "/audit-log");
   ```
   `200`, with `finding.commented`, `finding.assigned` and `finding.status_changed` newest
   first. `finding.commented` doesn't hold the comment's text.
10. Delete the test org as in B7 step 10.

### B9. Try an AI explanation
Each analyzed upload's most severe findings (up to 20) go to the triage queue, and the `triage`
worker explains them with gpt-oss-20b on Bedrock in Stockholm. The findings pages come in Plan 6;
until then you read the explanation from the API.
1. Do B7 steps 1 to 5: sign in, create the "Analysis Test" org, make and upload the port scan, and
   wait until the upload says `analyzed`.
2. Wait 30 seconds (the triage worker's first run starts cold), then read the finding:
   ```js
   const list = await api("GET", "/orgs/" + org.id + "/findings");
   const finding = await api("GET", "/orgs/" + org.id + "/findings/" + list.findings[0].id);
   finding.ai_analysis;
   ```
   Expect `status: "succeeded"`, `provider: "aws.bedrock"`, `model_id: "openai.gpt-oss-20b-1:0"`,
   `prompt_version: "v1"`, the tokens and latency, and `cost_usd` below `"0.002"` (a fifth of a
   cent).
   - `null`: wait a minute and read it again. Still `null` after 5 minutes: see "An AI explanation
     is missing" in Part C.
   - `status` `"failed"` or `"skipped_budget"`: look up its `error_code` in the same section.
3. Read what the model wrote:
   ```js
   finding.ai_analysis.output;
   ```
   It has a `summary`, `why_it_matters`, `likely_benign_explanations`, `recommended_next_steps`,
   `attack_techniques`, a `severity_assessment`, a `confidence` and `insufficient_evidence`. It's
   AI-generated: the checks only let it name `203.0.113.9`, `10.0.0.5` and the ports in the file.
   Tell Claude how it reads, with its `output_tokens`: that helps Plan 5d's evals.
4. Read the finding's history:
   ```js
   finding.events.map((event) => event.type);
   ```
   It ends with `ai_explained`. If the model named a technique, `finding.techniques` lists it with
   `source: "ai"` and its rationale.
5. In Grafana, open **Explore → Tempo** and run
   `{ resource.service.name = "nettriage-triage" && resource.deployment.environment.name = "dev" }`.
   There is a `triage.explain` trace with `triage.generate` inside it. `triage.generate` shows
   `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and
   `gen_ai.response.finish_reasons`, and `triage.explain` links to the `analyze.upload` trace.
6. Pause the AI and watch it skip:
   1. `aws login --profile nettriage` (skip this if you're signed in), then `just pause-ai`.
      Expected:
      `AI explanations in dev are paused (/nettriage/dev/kill/ai-enabled = false). The triage worker picks this up within a minute.`
   2. Wait a minute, repeat B7 steps 3 and 4 (a new upload in the same org), and wait 30 seconds.
   3. Read the newest finding as in step 2: `ai_analysis` has `status: "skipped_budget"` and
      `error_code: "ai_disabled"`.
   4. `just resume-ai`. Expected: `AI explanations in dev are on (… = true). …`
7. Delete the test org as in B7 step 10.

### B10. Try a re-run, a rating and the usage
1. Do B7 steps 1 to 5 (sign in, create the "Analysis Test" org, upload the port scan, and wait
   until it says `analyzed`), then read the finding as in B9 step 2:
   ```js
   const list = await api("GET", "/orgs/" + org.id + "/findings");
   const f = "/orgs/" + org.id + "/findings/" + list.findings[0].id;
   (await api("GET", f)).ai_analysis;
   ```
2. Ask for the explanation again. The `{}` is an empty body: CloudFront needs one, with its
   hash, on every POST.
   ```js
   await api("POST", f + "/ai-analyses", {});
   ```
   - If the analysis in step 1 had `status: "succeeded"`: `200` with `status: "explained"` and
     the same answer. Nothing is spent.
   - Otherwise (for example `failed` while AWS still limits Bedrock): `202` with
     `status: "queued"`. Read the finding again after a minute: the triage worker has tried again.
3. If it succeeded, rate it:
   ```js
   await api("PUT", f + "/ai-analyses/" + (await api("GET", f)).ai_analysis.id + "/feedback", { feedback: "up" });
   ```
   `200`, with `feedback: "up"` and your user ID in `feedback_by`. An analysis that didn't
   succeed answers `409`.
4. Read the org's AI usage:
   ```js
   await api("GET", "/orgs/" + org.id + "/usage");
   ```
   `200`, with today in `days` (its `calls`, tokens and `cost_usd`) and the `totals`, once Bedrock
   has answered at least once. Before that, `days` is empty and the totals are 0.
5. Read the audit log as in B8 step 9: a queued re-run is an `ai.rerun_requested` event.
6. Delete the test org as in B7 step 10.

### B11. Try the web app
1. Open `https://<id>.cloudfront.net/`. NetTriage's landing page opens, in the deck's navy and
   teal.
2. Click **Sign in / Sign up** and sign in with your password and a fresh code from the
   authenticator app. You land on **Your organizations**.
3. Under **Create an organization**, type `Web Test` and click **Create organization**. The
   organization opens on its **Members** page, with you as its Owner.
4. Under **Invitations**, type an email address that has no NetTriage account yet, keep
   **Viewer**, and click **Invite**. The invitation's link appears once; click **Copy link**.
5. Optional, to try accepting it: open a private window, paste the link, and sign up with that
   email address (it needs a password and an authenticator app of its own). You land on
   **Web Test**'s **Members** page as a Viewer, with nothing you can change. Close the private
   window. If you skip this, click **Revoke** next to the invitation.
6. Click the **Settings** tab. Rename the organization to `Web Test (2) & Bob's` and click
   **Save**: the new name shows at the top at once. (The punctuation checks that the name gets
   through CloudFront's signing when you delete.)
7. Under **Delete this organization**, type `Web Test (2) & Bob's` and click
   **Delete organization**. You're back on **Your organizations**, without it.
8. Click **Account settings** at the top: your email address shows. Click **Sign out**: you're
   signed out of NetTriage and of Cognito, and the landing page offers **Sign in / Sign up**.

## Part C: when things go wrong

### Pause uploads in an emergency
Uploads have a kill switch in SSM (spec §9.7). Flipping it needs your AWS session.
1. `aws login --profile nettriage`
2. Pause: `just pause-uploads`. Expected:
   `Uploads in dev are paused (/nettriage/dev/kill/uploads-enabled = false). The API picks this up within a minute.`
3. Resume later: `just resume-uploads`. Expected: `Uploads in dev are on (… = true). …`

Within a minute, new uploads get `503` ("Uploads are paused for now"); files already uploaded are
kept. A deploy never switches uploads back on.

### Pause AI explanations in an emergency
AI explanations have a kill switch in SSM (spec §9.7). Flipping it needs your AWS session.
1. `aws login --profile nettriage`
2. Pause: `just pause-ai`. Expected:
   `AI explanations in dev are paused (/nettriage/dev/kill/ai-enabled = false). The triage worker picks this up within a minute.`
3. Resume later: `just resume-ai`. Expected: `AI explanations in dev are on (… = true). …`

Within a minute, the triage worker stops calling Bedrock: new findings get `skipped_budget` with
`error_code: "ai_disabled"`, and answers it already has are still shown. Uploads and analysis
carry on. A deploy never switches the AI back on.

### An AI explanation is missing
When Bedrock is throttled, slow or down, the triage worker hands the finding back, and SQS
delivers it again 24 minutes later, three times at most. The first call of a day can take
minutes while Bedrock prepares the answer's schema, so a first try may time out. On the third
delivery the worker stores the failure: the finding's `ai_analysis` has `status: "failed"` and
an `error_code` (step 2), and the message is done.

Anything else that goes wrong (the database stays down past its retries, or the worker crashes
or times out) leaves `ai_analysis` at `null`. After three deliveries that message waits in
`nettriage-dev-triage-dlq` for 14 days; once the cause is fixed, send it back with **Start DLQ
redrive** on that queue, as for the analyze queue in "An upload isn't analyzed".
1. In the AWS console, with the Region set to Europe (Stockholm), open **CloudWatch → Log groups
   → /aws/lambda/nettriage-dev-triage**, and open the log stream from around the upload's time.
   Look for `finding_explain_later` and `triage_failed` lines, and send Claude their
   `error_code` (the logs never hold the finding's data, the prompt or the answer).
2. A finding's `ai_analysis.error_code` says why it has no explanation:

   | `error_code` | What it means |
   |---|---|
   | `ai_disabled` | The AI switch is off: `just resume-ai` |
   | `budget_exhausted_org` | The org used its 100,000 tokens today; the budget resets at midnight UTC |
   | `budget_exhausted_global` | All orgs together spent $0.50 today; the cap resets at midnight UTC |
   | `budget_unavailable` | DynamoDB couldn't be read; send Claude the time |
   | `provider_denied` | Bedrock refused the worker: the $5 budget action ran (next section), or the role lacks a permission. Send Claude the time |
   | `provider_rejected` | Bedrock refused the request itself; send Claude the time |
   | `provider_throttled`, `provider_timeout`, `provider_unavailable` | Bedrock failed three times in a row. Ask for the explanation again as in B10 step 2; the app's button comes in Plan 6. If every finding fails with `provider_throttled`, see "Bedrock refuses every call" below |
   | `checks_failed` (with `status: "invalid_output"`) | The model's answer failed the checks twice, so nothing was added to the finding. Send Claude the finding's `id` |

### Bedrock refuses every call: "Too many tokens per day"
AWS starts new accounts with Bedrock limits that refuse every call. The playground then answers
`ThrottlingException: Too many tokens per day, please wait before trying again.` on the first
prompt, and every finding ends as `failed` with `provider_throttled`. Waiting doesn't help, and
Service Quotas can't fix it: on 2026-10-02 the account-wide `Cross-Model Max Tokens Per Day` was
150,000,000 in Stockholm, and gpt-oss-20b had no quota of its own listed. AWS Support has to
lift the limits.
1. Open the case form directly at
   https://support.console.aws.amazon.com/support/home#/case/create?issueType=customer-service
   (Account and billing; free on every support plan). Don't start from the Amazon Q chat: the
   account's policies block its case summary (`support-console:GetIssueTextSummary`).
2. Pick the closest category (for example, other account questions), and ask AWS to verify the
   account and lift the initial Amazon Bedrock limits. Say which model and Region fail
   (`openai.gpt-oss-20b-1:0`, on demand, eu-north-1), that the account-wide daily quota isn't
   the limit, and the use: at most 2 calls at once of about 5,000 tokens each, with AI spend
   capped at $0.50 a day.
3. When AWS answers, send a short prompt in the Bedrock playground (Stockholm, gpt-oss-20b). A
   reply means the limits are lifted; then run B9 again.

### Bedrock was cut off at $5
An email from AWS Budgets says the month's usage passed $5, and the `nettriage-deny-bedrock`
policy is now attached to `nettriage-dev-triage`: new findings get `provider_denied`. Decide
with Claude before undoing it, because something spent far more than the AI budgets allow. To
undo it, open **IAM → Roles → nettriage-dev-triage → Permissions**, select
`nettriage-deny-bedrock` and choose **Remove**.

### An upload isn't analyzed
The worker gets each upload from the `nettriage-dev-analyze` queue. If it fails on one, SQS gives
it the upload again 30 minutes later. After the third failure, the upload shows `failed` with
`NetTriage couldn't analyze this file after three tries. Upload it again later.`, and the message
moves to `nettriage-dev-analyze-dlq`, where it waits 14 days. If the worker crashed or timed out
instead, the upload stays `processing` (Plan 7's daily job will mark those failed).
1. In the AWS console, with the Region set to Europe (Stockholm), open **CloudWatch → Log groups
   → /aws/lambda/nettriage-dev-analyze**, and open the log stream from around the upload's time.
   Look for `analyze_failed` lines and send Claude their `error_code` (the logs never hold the
   file's lines).
2. After the fix is deployed:
   - an upload that shows `failed`: upload the file again;
   - an upload still `processing`: send it back through the worker. Open **Simple Queue Service →
     nettriage-dev-analyze-dlq**, choose **Start DLQ redrive**, keep **Redrive to source
     queue(s)**, and choose **DLQ redrive**. The worker picks the upload up where it left off; no
     finding is stored twice.

An upload that stays `pending_upload` even though its PUT returned `200` never reached the worker:
send Claude the upload's `id`.

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
| `STOP: Couldn't give the database role app_api a login …` (or `app_analyze`, `app_triage`) | Check the stored string (A7), then send the output to Claude |
| `STOP: Syncing reference data failed; nothing in AWS changed. …` | The migrations ran, but the detectors and ATT&CK techniques weren't loaded. Send the output to Claude |
| `FAIL  Grafana OTLP endpoint in terraform.tfvars` | Put your endpoint in `terraform.tfvars` (A3) |
| `FAIL  Triage model on Bedrock` … `isn't offered in eu-north-1` or `isn't active and on demand` | Bedrock no longer offers the model there. Send the output to Claude, who picks another model with you |
| `PASS  Triage model on Bedrock  … (LEGACY: Bedrock will retire it; plan a switch)` | The deploy goes on: Bedrock keeps a legacy model working for at least six months. Tell Claude, who plans the switch to another model with you |
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
| `Too Many Requests` with `"status": 429` | Too many sign-in attempts or requests from your IP or account, or more than 5 uploads started at once in one org (20 a day), or more than 50 triage changes and comments at once in one org (500 a day). Wait the number of seconds in the `Retry-After` header (a minute at most for sign-in), then retry |
| `412` "This finding changed since you read it" | Someone changed the finding after you read it. Read it again (its `ETag` header is the new version), then repeat the change with that ETag |
| `428` "Send If-Match with the ETag of the finding you read" | A status or assignee change needs the finding's `ETag` in an `If-Match` header, as in B8 |
| `503` "Uploads are paused for now" | The uploads kill switch is off. Resume it as in "Pause uploads in an emergency" if that's not intended |
| Cognito's verification email never arrives | Check spam. Cognito's built-in sender allows about 50 emails a day per account; wait until tomorrow if many sign-ups ran today |
| `Error acquiring the state lock` | Another plan or deploy is running, or one was interrupted. Wait a minute and retry; if it persists, send the lock ID to Claude |
| `` STOP: `terraform apply` failed with exit code 1. `` | Terraform's own error is printed above this line (`apply` shares the terminal), so scroll up and read it. If it's `Error acquiring the state lock`, see that row; otherwise send the output to Claude. Terraform may have made some changes before failing; the next plan or deploy shows what's left |
| Terraform's `Error: … Unable to validate the following destination configurations` (the uploads bucket's notification) | S3 checked the analyze queue's new policy before it took effect. Run `just deploy-dev` again; Terraform creates what's left |
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
