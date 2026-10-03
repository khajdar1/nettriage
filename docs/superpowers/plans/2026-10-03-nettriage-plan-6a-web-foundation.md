# NetTriage Plan 6a: The Web App's Foundation and Organizations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder page with the first real web app:
- a typed API client generated from the API's OpenAPI document;
- the landing page, with sign-in and the reasons a sign-in failed;
- the person's organizations, creating one, and account settings with "sign out everywhere";
- an organization's members, roles and invitations, and its settings (rename, leave, delete);
- accepting an invitation from its link, through sign-in when needed.

Plan 6b adds uploads, findings, triage, the AI panel, the audit log and usage. Plan 6c adds the public demo.

**Architecture:**
- **The OpenAPI document is committed** as `frontend/openapi.json`, exported from the FastAPI app with `just openapi`. openapi-typescript generates `frontend/src/api/schema.ts` from it.
  - A backend test fails when the document differs from the API.
  - `pnpm check:api` fails when the generated types differ from the document.
- **The client** is openapi-fetch with one middleware. It adds:
  - the body's SHA-256 for CloudFront's origin access control, sending `{}` on a POST or PUT with no inputs;
  - the session's CSRF token on state-changing requests.
  
  `unwrap()` turns RFC 9457 Problem Details into an `ApiError`, which every page shows with its reference.
- **Server state is TanStack Query.** `useMe()` is the session; a 401 from any read asks the person to sign in again, and brings them back to the same page. Routing is React Router.
- **No component library.** The deck's look is in one hand-written stylesheet, because the CSP allows no inline styles. Lint forbids `style` props and `dangerouslySetInnerHTML`.

**Tech Stack:** React 19, TypeScript 6 (strict), Vite 8 · React Router 8, TanStack Query 5, openapi-fetch 0.17, openapi-typescript 7 · @fontsource (Space Grotesk, IBM Plex Sans, JetBrains Mono) · Vitest 5, Testing Library, user-event · FastAPI (the OpenAPI export).

**Spec:** `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (revision 2). Read it alongside this plan; section numbers (§) refer to it. This plan implements:
- §2.2 features 1 and 2 in the browser: sign in and out, sign out everywhere, and organizations;
- §6.2's CSRF token, and §6.3's invitations;
- §7's SPA request headers and Problem Details;
- §10's landing, `/invite`, `/app`, members and settings pages, its libraries, its security rules, its states and accessibility;
- §11.4's contract checks.

**Plan series:** Plan 6 of 7 ("frontend") is in three parts (the owner's decision, 2026-10-03):
- **6a (this plan): the foundation and organizations;**
- 6b: uploads, findings, triage, the AI panel, the audit log and usage, with `sort` added to the findings API;
- 6c: the public demo, written once AWS lifts the Bedrock limits, so the demo's AI explanations are real.

Plan 5d (the evals) also waits for Bedrock. Plan 7 adds the Playwright smoke tests after each deploy (the owner's decision).

**Branch:** `plan-6a/web-foundation`, from `main` at `457095f` or later.

## Global Constraints

- **Stack.** Node 24 with pnpm 12 (`packageManager` in `frontend/package.json`). TypeScript strict, with `noUncheckedIndexedAccess`. ESLint (typescript-eslint strict) and Prettier (print width 100) pass. Python 3.14 for the export, with mypy `--strict` and Ruff. Work test-first.
- **Commands on this Windows machine.** Run Python tools as modules (`uv run python -m …`). Frontend commands run in `frontend/` with `pnpm`. Backend tests need the local Postgres, which `just test` starts.
- **CSP (§6.7, §10).** The build has no inline scripts or styles (`pnpm check:csp`). No `style` props, no `dangerouslySetInnerHTML`, and fonts are served from the app itself.
- **Links (§10).** External links use `target="_blank"` with `rel="noopener noreferrer"`.
- **Requests (§7).**
  - A request with a body carries `x-amz-content-sha256`, and a POST or PUT with no inputs sends `{}`.
  - POST, PUT, PATCH and DELETE carry `X-CSRF-Token`.
  - A DELETE has no body.
  - A create sends an `Idempotency-Key`.
- **States (§10).** Loading and empty states. A Problem Details error is shown with its reference (`trace_id`), and a 429 with its `Retry-After` time.
- **Accessibility (§10).** WCAG 2.2 AA is the target:
  - every control has a label;
  - pages have one `h1` and a title;
  - a skip link and visible focus;
  - text contrast of at least 4.5:1.
  
  Tests find elements by role and name.
- **Permissions (§6.4).** The app hides what a role can't do, but the API decides: every refusal is shown.
- **Owner-only commands.** Claude never runs `aws login`, `just bootstrap`, `just store-*`, `just pause-*`, `just resume-*` or `just deploy-*`.
- **Commits.** Use Conventional Commits. Every commit made by Claude ends with the trailer `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Decisions this plan makes

1. **Plan 6 is in three parts** (the owner's decision, 2026-10-03). 6c, the demo, waits for live Bedrock answers.
2. **The look matches the directors' deck** (the owner's decision):
   - navy `#0F1B2A`, teal `#5CC8C0` and `#0B6E6E`, and off-white `#F4F5F1`;
   - Space Grotesk for headings, IBM Plex Sans for text and JetBrains Mono for code;
   - only the Latin subsets are bundled; other scripts fall back to system fonts.
3. **The API contract is committed:**
   - `frontend/openapi.json`, exported by `nettriage.entrypoints.api.openapi_document` with the API's version `1`, never a build's;
   - `frontend/src/api/schema.ts`, generated from it;
   - `just openapi` regenerates both, and CI checks both.
4. **No component library.** Libraries that inject styles at runtime break `style-src 'self'`. Every style lives in `src/styles.css`.
5. **The client:**
   - a read is retried twice on a network error or a 5xx, never on a 4xx;
   - a 401 shows "Sign in to continue", whose link brings the person back to the same page. It never redirects on its own, so it can never loop.
6. **The pages:**
   - the landing page has no **View the live demo** link yet: it comes with the demo, in 6c;
   - `/app` is the org switcher, and the product name in the top bar returns to it;
   - an organization's frame shows its name, the person's role, and the tabs **Members** and **Settings**;
   - `/app/orgs/:org` opens **Members** until 6b adds findings;
   - the new page `/app/orgs/:org/settings` lets Owners and Admins rename, anyone leave, and Owners delete;
   - `/app/settings` is called **Account settings**, so it isn't confused with the organization's **Settings** tab.
7. **Invitations:**
   - an invitation's link is shown once, with a copy button, and the role defaults to Viewer;
   - `/invite` takes the token out of the address bar at once, keeps it in the tab's `sessionStorage` while the person signs in, and accepts it once they're signed in.
8. **Destructive actions:**
   - removing a member and leaving are confirmed inline;
   - deleting an organization is confirmed by typing its exact name;
   - there are no modal dialogs.
9. **A form's `Idempotency-Key` lasts as long as the form.** A resent create returns the organization already made. A refused request is forgotten by the API, so retrying with a corrected name works.
10. **Tests** stand in for the API by replacing `fetch` (`src/test/fakeApi.ts`). Vitest restores it, and resets spies, after each test.

## Review Focus

1. **CloudFront refusing a request it can't verify.** A body without its hash, or a POST with no body, is rejected at the edge. Tests: Task 2 `a request with a body carries the body's SHA-256` and `a POST with no inputs sends an empty JSON body and its hash`.
2. **A session that ends during a visit.** The person must be asked to sign in, and brought back to the same page. Tests: Task 4 `signed out, the app asks to sign in and comes back to the same page`; Task 5 `a session that ends during a visit asks to sign in again`.
3. **An invitation token leaking, or lost across sign-in.** It must leave the address bar, survive the trip through Cognito, and be accepted exactly once. Tests: Task 7 `signed out, it keeps the code, clears the address bar and asks to sign in`, `back from signing in, it accepts with the kept code and opens the organization` and `a link opened while signed in is accepted once, right away`.
4. **A create sent twice making two organizations.** Tests: Task 2 `each create gets its own idempotency key, sent as a header`; Task 4 `creating an organization sends its name once, then opens it`.
5. **Controls the API will refuse, or a refusal nobody sees.** The UI must follow §6.4 and show every refusal with its reference. Tests:
   - Task 5: `an admin changes only analysts and viewers, and never themselves` and `a change the API refuses says why`;
   - Task 6: `a member sees only the settings their role allows` and `the last owner can't leave, and is told why`.

## Owner prerequisites

- **None to build or review.** The tests stand in for the API.
- **After the merge:** runbook B2 (the deploy publishes the new web build; no migrations), then B11, the new walk through the app.

## File map

| File | Responsibility | Task |
|---|---|---|
| `backend/src/nettriage/entrypoints/api/openapi_document.py`, `frontend/openapi.json`, `frontend/src/api/schema.ts` | the API contract, exported and generated | 1 |
| `frontend/src/api/client.ts`, `problem.ts`, `src/test/fakeApi.ts` | the typed client, its headers, readable errors; the fake API | 2 |
| `frontend/src/app/`, `src/auth/session.ts`, `src/pages/Landing.tsx`, `NotFound.tsx`, `src/styles.css`, `src/main.tsx` | routes, server state, the session, the landing page, the look | 3 |
| `frontend/src/app/RequireSession.tsx`, `AppLayout.tsx`, `src/pages/Orgs.tsx`, `Settings.tsx`, `src/ui/` | the signed-in frame, the organizations list, account settings | 4 |
| `frontend/src/orgs/`, `src/pages/org/OrgLayout.tsx`, `Members.tsx`, `MemberRow.tsx`, `Invitations.tsx` | an organization's frame, members, roles and invitations | 5 |
| `frontend/src/pages/org/OrgSettings.tsx` | rename, leave, delete | 6 |
| `frontend/src/pages/Invite.tsx` | accepting an invitation | 7 |
| `docs/…/spec`, `docs/runbooks/setup-and-deploy.md`, `README.md` | the decisions, and runbook B11 | 8 |

Tests sit next to the code they test (`*.test.ts`, `*.test.tsx`).

---

### Task 1: The API contract, committed and checked

**Files:**
- Create: `backend/src/nettriage/entrypoints/api/openapi_document.py`, `frontend/openapi.json` and `frontend/src/api/schema.ts` (both generated)
- Modify: `frontend/package.json`, `frontend/pnpm-lock.yaml`, `frontend/.prettierignore`, `frontend/eslint.config.js`, `justfile`, `.github/workflows/ci.yml`
- Test: `backend/tests/api/test_openapi_document.py`

**Interfaces:**
- Consumes: `create_app(settings, services)` from `nettriage.entrypoints.api.app`, and `Settings` from `nettriage.platform.config`.
- Produces:
  - in `nettriage.entrypoints.api.openapi_document`: `API_VERSION = "1"`, `openapi_document() -> dict[str, Any]`, `render(document) -> str` and `main(argv)`;
  - `frontend/openapi.json`, and `frontend/src/api/schema.ts`, whose `paths` and `components` types every later task imports;
  - the `generate:api` and `check:api` scripts, and `just openapi`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_openapi_document.py`:
```python
"""The OpenAPI document the frontend's typed client is generated from (spec §7, §11.4)."""

from pathlib import Path

from nettriage.entrypoints.api.openapi_document import main, openapi_document, render

COMMITTED = Path(__file__).resolve().parents[3] / "frontend" / "openapi.json"


def test_the_committed_document_matches_the_api() -> None:
    assert COMMITTED.read_text(encoding="utf-8") == render(openapi_document()), (
        "The API changed: run `just openapi`, then commit frontend/openapi.json and "
        "frontend/src/api/schema.ts."
    )


def test_the_document_names_the_apis_version_not_a_builds() -> None:
    assert openapi_document()["info"]["version"] == "1"


def test_every_route_is_under_api() -> None:
    paths = list(openapi_document()["paths"])

    assert paths
    assert all(path.startswith("/api/") for path in paths)


def test_main_writes_the_document(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"

    main(["openapi_document", str(out)])

    assert out.read_text(encoding="utf-8") == render(openapi_document())
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run python -m pytest tests/api/test_openapi_document.py`
Expected: FAIL. Collection stops with 1 error: `No module named 'nettriage.entrypoints.api.openapi_document'`.

- [ ] **Step 3: Write the export**

`backend/src/nettriage/entrypoints/api/openapi_document.py`:
```python
"""The API's OpenAPI document (spec §7, §11.4).

The repository keeps it at `frontend/openapi.json`, and the frontend's typed client is generated
from it: a change to the API shows in the pull request's diff, and the frontend stops compiling
until it handles the change. After changing the API, run `just openapi`.

Usage, from `backend/`:
    python -m nettriage.entrypoints.api.openapi_document ../frontend/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

from nettriage.entrypoints.api.app import create_app
from nettriage.entrypoints.api.services import Services
from nettriage.platform.config import Settings

# The API's version, not a build's: the document changes only when the API does.
API_VERSION = "1"


def openapi_document() -> dict[str, Any]:
    """Building the document calls no service, so the app is given none."""
    app = create_app(Settings(stage="local", version=API_VERSION), cast(Services, None))
    return app.openapi()


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> None:
    out = Path(argv[1])
    out.write_text(render(openapi_document()), encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
```

- [ ] **Step 4: Add the client generator, its scripts and its checks**

Run: `cd frontend && pnpm add -D 'openapi-typescript@^7.13.0'`
Expected: `+ openapi-typescript 7.13.0` (or a later 7.x). pnpm warns that openapi-typescript wants TypeScript 5; it works with TypeScript 6, and `check:api` below proves it.

In `frontend/package.json`, replace:
```json
    "check:csp": "node scripts/check-csp.mjs dist/index.html"
  },
```
with:
```json
    "check:csp": "node scripts/check-csp.mjs dist/index.html",
    "generate:api": "openapi-typescript openapi.json -o src/api/schema.ts",
    "check:api": "openapi-typescript openapi.json -o src/api/schema.ts --check"
  },
```

In `frontend/.prettierignore`, replace:
```
dist
pnpm-lock.yaml
```
with:
```
dist
# Generated: the API's OpenAPI document, and the client types made from it (`just openapi`).
openapi.json
src/api/schema.ts
pnpm-lock.yaml
```

In `frontend/eslint.config.js`, replace:
```js
export default defineConfig([
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
```
with:
```js
export default defineConfig([
  // src/api/schema.ts is generated from openapi.json by `just openapi`.
  { ignores: ["dist", "node_modules", "src/api/schema.ts"] },
  js.configs.recommended,
```

In `justfile`, replace:
```
web-check:
    cd frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm test && pnpm test:scripts && pnpm build && pnpm check:csp

```
with:
```
web-check:
    cd frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm check:api && pnpm test && pnpm test:scripts && pnpm build && pnpm check:csp

# API: export the OpenAPI document and regenerate the frontend's typed client from it
openapi:
    cd backend && uv run python -m nettriage.entrypoints.api.openapi_document ../frontend/openapi.json
    cd frontend && pnpm generate:api

```

In `.github/workflows/ci.yml`, replace:
```yaml
      - run: pnpm lint
      - run: pnpm test
```
with:
```yaml
      - run: pnpm lint
      - name: Check that the typed client matches the API's OpenAPI document
        run: pnpm check:api
      - run: pnpm test
```

- [ ] **Step 5: Generate the document and the client types**

Run: `just openapi`
Expected: `wrote ..\frontend\openapi.json`, then `openapi.json → src/api/schema.ts`. `frontend/openapi.json` starts with `"openapi": "3.1.0"`, and `frontend/src/api/schema.ts` starts with `export interface paths {`.

- [ ] **Step 6: Run the checks**

Run: `just lint test && cd frontend && pnpm lint && pnpm check:api && pnpm test && pnpm build`
Expected:
- the backend: lint clean, and `1054 passed, 1 skipped`;
- the frontend: lint clean; `check:api` prints only its banner and exits 0; `1 passed`; and the build succeeds.

- [ ] **Step 7: Commit**

```bash
git add backend .github justfile frontend
git commit -m "feat(api): export the OpenAPI document and generate the frontend's typed client from it" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The typed API client

**Files:**
- Create: `frontend/src/api/client.ts`, `frontend/src/api/problem.ts`, `frontend/src/test/fakeApi.ts`
- Modify: `frontend/package.json`, `frontend/pnpm-lock.yaml`, `frontend/vite.config.ts`
- Test: `frontend/src/api/client.test.ts`, `frontend/src/api/problem.test.ts`

**Interfaces:**
- Consumes: Task 1's `paths` from `src/api/schema.ts`.
- Produces:
  - in `src/api/client.ts`: `api` (the openapi-fetch client), `setCsrfToken(token: string | null)`, `idempotencyKey(): string`, `sha256Hex(data)` and the `signRequests` middleware;
  - in `src/api/problem.ts`: `ApiError` (`status`, `title`, `detail`, `reference`, `retryAfterSeconds`), `apiError(response, body)`, `unwrap(result)` and `errorMessage(error): string`;
  - in `src/test/fakeApi.ts`: `fakeApi(routes) -> FakeApi` (`requests`, `on(route, handler)`), with the `Reply` and `Handler` types.

- [ ] **Step 1: Write the failing tests**

`frontend/src/test/fakeApi.ts`:
```ts
/**
 * A stand-in for the API in tests: answers `fetch` from a table of routes ("GET /api/v1/me")
 * and keeps every request it was sent. Vitest restores the real `fetch` after each test.
 */
import { vi } from "vitest";

export interface Reply {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

export type Handler = Reply | ((request: Request) => Reply | Promise<Reply>);

export interface FakeApi {
  /** Every request, in order, as sent (bodies still readable). */
  requests: Request[];
  on(route: string, handler: Handler): void;
}

function respond(reply: Reply): Response {
  const status = reply.status ?? 200;
  const empty = reply.body === undefined || status === 204;
  const type = status >= 400 ? "application/problem+json" : "application/json";
  return new Response(empty ? null : JSON.stringify(reply.body), {
    status,
    headers: { "content-type": type, ...reply.headers },
  });
}

export function fakeApi(routes: Record<string, Handler> = {}): FakeApi {
  const table = new Map(Object.entries(routes));
  const requests: Request[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    requests.push(request.clone());
    const route = `${request.method} ${new URL(request.url).pathname}`;
    const handler = table.get(route);
    if (handler === undefined) {
      return respond({ status: 404, body: { title: "Not Found", detail: `No fake for ${route}` } });
    }
    return respond(typeof handler === "function" ? await handler(request) : handler);
  });
  return { requests, on: (route, handler) => void table.set(route, handler) };
}
```

`frontend/src/api/client.test.ts`:
```ts
import { afterEach, expect, test } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { api, idempotencyKey, setCsrfToken } from "./client";

// SHA-256 of the exact bytes sent, as CloudFront's origin access control needs them (spec §7).
const ACME_SHA256 = "e3bfc991ec09804fb70da1e9797418443fba7cac9d85f2639242a5ecfadd51c7";
const EMPTY_BODY_SHA256 = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a";
const ORG = "01a10333-a115-741b-91ff-41d6e310d817";
const FINDING = "01a10334-1574-7234-9a97-6645f0dfa085";

afterEach(() => setCsrfToken(null));

function sent(requests: Request[]): Request {
  const request = requests.at(-1);
  if (request === undefined) {
    throw new Error("nothing was sent");
  }
  return request;
}

test("a request with a body carries the body's SHA-256", async () => {
  const fake = fakeApi({ "POST /api/v1/orgs": { status: 201, body: {} } });

  await api.POST("/api/v1/orgs", { body: { name: "Acme Security" } });

  const request = sent(fake.requests);
  expect(await request.text()).toBe('{"name":"Acme Security"}');
  expect(request.headers.get("x-amz-content-sha256")).toBe(ACME_SHA256);
});

test("a POST with no inputs sends an empty JSON body and its hash", async () => {
  const path = `/api/v1/orgs/${ORG}/findings/${FINDING}/ai-analyses`;
  const fake = fakeApi({ [`POST ${path}`]: { status: 202, body: {} } });

  await api.POST("/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses", {
    params: { path: { org_id: ORG, finding_id: FINDING } },
  });

  const request = sent(fake.requests);
  expect(await request.text()).toBe("{}");
  expect(request.headers.get("content-type")).toBe("application/json");
  expect(request.headers.get("x-amz-content-sha256")).toBe(EMPTY_BODY_SHA256);
});

test("a GET or a DELETE has no body and no hash", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: {} },
    [`DELETE /api/v1/orgs/${ORG}`]: { status: 204 },
  });

  await api.GET("/api/v1/me");
  await api.DELETE("/api/v1/orgs/{org_id}", {
    params: { path: { org_id: ORG }, query: { confirm_name: "Acme Security" } },
  });

  for (const request of fake.requests) {
    expect(await request.text()).toBe("");
    expect(request.headers.has("x-amz-content-sha256")).toBe(false);
  }
});

test("state-changing requests carry the CSRF token and reads don't", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: {} },
    "POST /api/v1/orgs": { status: 201, body: {} },
    [`PATCH /api/v1/orgs/${ORG}`]: { body: {} },
  });
  setCsrfToken("csrf-1");

  await api.GET("/api/v1/me");
  await api.POST("/api/v1/orgs", { body: { name: "Acme Security" } });
  await api.PATCH("/api/v1/orgs/{org_id}", {
    params: { path: { org_id: ORG } },
    body: { name: "Acme" },
  });

  expect(fake.requests.map((request) => request.headers.get("X-CSRF-Token"))).toEqual([
    null,
    "csrf-1",
    "csrf-1",
  ]);
});

test("each create gets its own idempotency key, sent as a header", async () => {
  const fake = fakeApi({ "POST /api/v1/orgs": { status: 201, body: {} } });
  const key = idempotencyKey();

  await api.POST("/api/v1/orgs", {
    body: { name: "Acme Security" },
    headers: { "Idempotency-Key": key },
  });

  expect(key).toMatch(/^[0-9a-f-]{36}$/);
  expect(idempotencyKey()).not.toBe(key);
  expect(sent(fake.requests).headers.get("Idempotency-Key")).toBe(key);
});
```

`frontend/src/api/problem.test.ts`:
```ts
import { expect, test } from "vitest";
import { type ApiError, apiError, errorMessage, unwrap } from "./problem";

function problem(status: number, body: unknown, headers: Record<string, string> = {}): ApiError {
  return apiError(new Response(null, { status, headers }), body);
}

test("a problem reads as its detail, with the reference support needs", () => {
  const error = problem(409, {
    title: "Conflict",
    detail: "An organization keeps at least one owner.",
    trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
  });

  expect(errorMessage(error)).toBe(
    "An organization keeps at least one owner. (reference 4bf92f3577b34da6a3ce929d0e0e4736)",
  );
});

test("a problem without a detail reads as its title", () => {
  expect(errorMessage(problem(403, { title: "Forbidden", detail: null, trace_id: null }))).toBe(
    "Forbidden",
  );
});

test.each([
  ["30", "Too many requests. Try again in 30 seconds."],
  ["1", "Too many requests. Try again in 1 second."],
  ["360", "Too many requests. Try again in 6 minutes."],
  [undefined, "Too many requests. Try again shortly."],
])("a 429 with Retry-After %s says when to try again", (retryAfter, message) => {
  const headers: Record<string, string> =
    retryAfter === undefined ? {} : { "Retry-After": retryAfter };

  expect(errorMessage(problem(429, { title: "Too Many Requests" }, headers))).toBe(message);
});

test("anything else reads as a connection problem", () => {
  expect(errorMessage(new TypeError("Failed to fetch"))).toBe(
    "Something went wrong. Check your connection and try again.",
  );
});

test("a body that isn't Problem Details still gives the status", () => {
  const error = problem(502, "<html>Bad Gateway</html>");

  expect([error.status, errorMessage(error)]).toEqual([502, "Request failed (502)"]);
});

test("unwrap returns the data of a success and throws the problem of a failure", () => {
  const ok = { data: { id: "1" }, response: new Response(null, { status: 200 }) };
  const failed = {
    error: { title: "Not Found", detail: "No such organization." },
    response: new Response(null, { status: 404 }),
  };

  expect(unwrap(ok)).toEqual({ id: "1" });
  expect(() => unwrap(failed)).toThrow(
    expect.objectContaining({ status: 404, detail: "No such organization." }),
  );
});
```

In `frontend/vite.config.ts`, replace:
```ts
    setupFiles: ["./src/test-setup.ts"],
    // Vitest's default include glob also matches scripts/check-csp.test.mjs,
```
with:
```ts
    setupFiles: ["./src/test-setup.ts"],
    // Tests stand in for the API by replacing fetch (src/test/fakeApi.ts); put it back after each.
    unstubGlobals: true,
    // Vitest's default include glob also matches scripts/check-csp.test.mjs,
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && pnpm exec vitest run src/api`
Expected: FAIL. Both files fail before any test runs: `Failed to resolve import "./client"` and `Failed to resolve import "./problem"`.

- [ ] **Step 3: Add openapi-fetch, and write the client**

Run: `cd frontend && pnpm add 'openapi-fetch@^0.17.0'`

`frontend/src/api/client.ts`:
```ts
/**
 * The typed API client (spec §7, §10), generated from the API's OpenAPI document.
 *
 * Every API request passes through CloudFront's origin access control, which signs it for the
 * Lambda function URL. A request with a body must carry the body's SHA-256 in
 * `x-amz-content-sha256`, so a POST or PUT with no inputs sends `{}` to have a body to sign.
 * State-changing requests carry the session's CSRF token (spec §6.2).
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

const STATE_CHANGING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const BODY_REQUIRED = new Set(["POST", "PUT"]);
const EMPTY_BODY = "{}";

let csrfToken: string | null = null;

/** The session's CSRF token, from `GET /api/v1/me`; `null` once signed out. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export async function sha256Hex(data: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export const signRequests: Middleware = {
  async onRequest({ request }) {
    let signed = request;
    let body: BufferSource = await request.clone().arrayBuffer();
    if (body.byteLength === 0 && BODY_REQUIRED.has(request.method)) {
      const headers = new Headers(request.headers);
      headers.set("Content-Type", "application/json");
      signed = new Request(request, { body: EMPTY_BODY, headers });
      body = new TextEncoder().encode(EMPTY_BODY);
    }
    if (body.byteLength > 0) {
      signed.headers.set("x-amz-content-sha256", await sha256Hex(body));
    }
    if (STATE_CHANGING.has(request.method) && csrfToken !== null) {
      signed.headers.set("X-CSRF-Token", csrfToken);
    }
    return signed;
  },
};

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  // Looked up on every call, so tests can stand in for the API.
  fetch: (request) => globalThis.fetch(request),
});
api.use(signRequests);

/** A key for one create: a retry of the same request returns the first result (spec §7). */
export function idempotencyKey(): string {
  return crypto.randomUUID();
}
```

`frontend/src/api/problem.ts`:
```ts
/**
 * API errors (spec §7, §10): RFC 9457 Problem Details become an `ApiError`, shown as a message
 * that includes the error's reference. A 429 says when to try again.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | null;
  readonly reference: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    title: string,
    detail: string | null,
    reference: string | null,
    retryAfterSeconds: number | null,
  ) {
    super(detail ?? title);
    this.name = "ApiError";
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.reference = reference;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** The error a response describes, from its Problem Details body and `Retry-After`. */
export function apiError(response: Response, body: unknown): ApiError {
  const problem: Record<string, unknown> =
    typeof body === "object" && body !== null ? { ...body } : {};
  const retryAfter = Number(response.headers.get("Retry-After"));
  return new ApiError(
    response.status,
    text(problem.title) ?? text(response.statusText) ?? `Request failed (${response.status})`,
    text(problem.detail),
    text(problem.trace_id),
    Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : null,
  );
}

/** openapi-fetch's result as its data, or a thrown `ApiError`. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok) {
    throw apiError(result.response, result.error);
  }
  return result.data as T;
}

function wait(seconds: number): string {
  if (seconds < 60) {
    return seconds === 1 ? "1 second" : `${seconds} seconds`;
  }
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

/** What the person reads: the problem, its reference for support, or when to try again. */
export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Check your connection and try again.";
  }
  if (error.status === 429) {
    return error.retryAfterSeconds === null
      ? "Too many requests. Try again shortly."
      : `Too many requests. Try again in ${wait(error.retryAfterSeconds)}.`;
  }
  const message = error.detail ?? error.title;
  return error.reference === null ? message : `${message} (reference ${error.reference})`;
}
```

- [ ] **Step 4: Run the checks**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `15 passed`.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat(web): the typed API client: CloudFront's body hash, the CSRF token, idempotency keys and readable problems" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The app shell, the session and the landing page

**Files:**
- Create:
  - `frontend/src/app/routes.tsx`, `frontend/src/app/queryClient.ts`, `frontend/src/auth/session.ts`;
  - `frontend/src/pages/Landing.tsx`, `frontend/src/pages/NotFound.tsx`;
  - `frontend/src/ui/ExternalLink.tsx`, `frontend/src/ui/usePageTitle.ts`;
  - `frontend/src/test/fixtures.ts`, `frontend/src/test/render.tsx`
- Modify: `frontend/src/main.tsx`, `frontend/src/styles.css`, `frontend/src/test-setup.ts`, `frontend/eslint.config.js`, `frontend/package.json`, `frontend/pnpm-lock.yaml`
- Delete: `frontend/src/App.tsx`, `frontend/src/App.test.tsx`
- Test: `frontend/src/auth/session.test.ts`, `frontend/src/app/queryClient.test.ts`, `frontend/src/pages/Landing.test.tsx`

**Interfaces:**
- Consumes: Task 2's `api`, `setCsrfToken`, `unwrap`, `ApiError` and `fakeApi`; Task 1's `components` schema types.
- Produces:
  - in `src/auth/session.ts`: `Me`, `Membership`, `ME_KEY`, `fetchMe()`, `useMe()`, `signInUrl(returnTo)`, `browser.go(url)` and `signOut({ everywhere })`;
  - `createQueryClient({ retries })` in `src/app/queryClient.ts`, and `routes: RouteObject[]` in `src/app/routes.tsx`, which later tasks extend;
  - `ExternalLink`, and `usePageTitle(title | null)`, which sets "<title> · NetTriage";
  - for tests: `renderAt(path) -> { router, client, user }`, and the fixtures `SIGNED_OUT` and `ME`;
  - the stylesheet's classes: `page`, `narrow`, `card`, `field`, `inline-form`, `table`, `badge`, `button` (`-primary`, `-danger`, `-quiet`), `notice` (`-error`, `-ok`), `actions`, `muted`, `visually-hidden` and `mono`.

- [ ] **Step 1: Add the app's libraries**

Run:
```bash
cd frontend
pnpm add 'react-router@^8.4.0' '@tanstack/react-query@^5.104.1' '@fontsource/space-grotesk@^5.3.0' '@fontsource/ibm-plex-sans@^5.3.0' '@fontsource/jetbrains-mono@^5.3.0'
pnpm add -D '@testing-library/user-event@^14.6.7'
```

- [ ] **Step 2: Write the failing tests**

`frontend/src/test-setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library unmounts after each test only when Vitest's globals are on; they're off here.
afterEach(() => cleanup());
```

`frontend/src/test/fixtures.ts`:
```ts
/** Data the fake API answers with, typed by the API's own schema. */
import type { Me } from "../auth/session";
import type { Reply } from "./fakeApi";

export const SIGNED_OUT: Reply = {
  status: 401,
  body: { type: "about:blank", title: "Unauthorized", status: 401, detail: null, trace_id: null },
};

export const ME: Me = {
  user: {
    id: "01a0e9e9-75d3-7462-9e22-4f476c3e802c",
    email: "ana@example.com",
    display_name: null,
  },
  memberships: [],
  csrf_token: "csrf-1",
};
```

`frontend/src/test/render.tsx`:
```tsx
import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { createQueryClient } from "../app/queryClient";
import { routes } from "../app/routes";

/** The whole app opened at `path`, as a person would see it; failed reads aren't retried. */
export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = createQueryClient({ retries: 0 });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, client, user };
}
```

`frontend/src/auth/session.test.ts`:
```ts
import { afterEach, expect, test, vi } from "vitest";
import { api, setCsrfToken } from "../api/client";
import { fakeApi } from "../test/fakeApi";
import { ME, SIGNED_OUT } from "../test/fixtures";
import { browser, fetchMe, signInUrl, signOut } from "./session";

afterEach(() => setCsrfToken(null));

async function csrfSentByAPost(): Promise<string | null> {
  const fake = fakeApi({ "POST /api/v1/orgs": { status: 201, body: {} } });
  await api.POST("/api/v1/orgs", { body: { name: "Acme Security" } });
  return fake.requests[0]?.headers.get("X-CSRF-Token") ?? null;
}

test("the signed-in person comes with the CSRF token later requests send", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  expect(await fetchMe()).toEqual(ME);
  expect(await csrfSentByAPost()).toBe("csrf-1");
});

test("signed out is null, and forgets the CSRF token", async () => {
  setCsrfToken("old");
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  expect(await fetchMe()).toBeNull();
  expect(await csrfSentByAPost()).toBeNull();
});

test("signing in starts at the API and comes back to the page asked for", () => {
  expect(signInUrl("/app/orgs/1/members")).toBe(
    "/api/auth/login?return_to=%2Fapp%2Forgs%2F1%2Fmembers",
  );
});

test.each([
  [false, "/api/auth/logout"],
  [true, "/api/auth/logout-all"],
])("signing out (everywhere: %s) ends the session, then Cognito's", async (everywhere, path) => {
  const logoutUrl = "https://auth.example.com/logout?client_id=abc";
  const fake = fakeApi({ [`POST ${path}`]: { body: { logout_url: logoutUrl } } });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  setCsrfToken("csrf-1");

  await signOut({ everywhere });

  expect(fake.requests[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  expect(go).toHaveBeenCalledWith(logoutUrl);
  expect(await csrfSentByAPost()).toBeNull();
});
```

`frontend/src/app/queryClient.test.ts`:
```ts
import { expect, test } from "vitest";
import { ApiError } from "../api/problem";
import { createQueryClient } from "./queryClient";

function retries(error: unknown, failures: number): boolean {
  const retry = createQueryClient().getDefaultOptions().queries?.retry;
  if (typeof retry !== "function") {
    throw new Error("retry should be a function");
  }
  return retry(failures, error as Error);
}

test("a read that failed on the network or the server is tried twice more", () => {
  const unavailable = new ApiError(503, "Service Unavailable", null, null, null);

  expect([retries(new TypeError("Failed to fetch"), 0), retries(unavailable, 1)]).toEqual([
    true,
    true,
  ]);
  expect(retries(unavailable, 2)).toBe(false);
});

test("a 4xx is never retried: a retry can't fix it", () => {
  for (const status of [400, 401, 403, 404, 409, 429]) {
    expect(retries(new ApiError(status, "No", null, null, null), 0)).toBe(false);
  }
});
```

`frontend/src/pages/Landing.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { ME, SIGNED_OUT } from "../test/fixtures";
import { renderAt } from "../test/render";

test("the landing page names the product and offers to sign in", async () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/");

  expect(screen.getByRole("heading", { level: 1, name: "NetTriage" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "Sign in / Sign up" })).toHaveAttribute(
    "href",
    "/api/auth/login?return_to=%2Fapp",
  );
  expect(document.title).toBe("NetTriage");
});

test("signed in, it opens the person's organizations instead", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/");

  expect(await screen.findByRole("link", { name: "Open your organizations" })).toHaveAttribute(
    "href",
    "/app",
  );
});

test.each([
  ["expired", "Your sign-in took too long, or started in another browser. Please sign in again."],
  ["failed", "Sign-in was cancelled or didn't finish. Please try again."],
  ["disabled", "This account is disabled."],
  ["unavailable", "Sign-in isn't available right now. Please try again in a few minutes."],
  ["limited", "Too many sign-in attempts. Please wait a minute, then try again."],
])("a sign-in that failed with %s says why", (reason, message) => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt(`/?sign_in=${reason}`);

  expect(screen.getByRole("alert")).toHaveTextContent(message);
});

test("a sign-in reason it doesn't know shows nothing", () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/?sign_in=<script>");

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("the source link opens in a new tab that can't reach back", () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/");

  const link = screen.getByRole("link", { name: "Source on GitHub" });
  expect(link).toHaveAttribute("href", "https://github.com/khajdar1/nettriage");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

test("an address that isn't a page says so and leads home", () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/no/such/page");

  expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to the home page" })).toHaveAttribute("href", "/");
  expect(document.title).toBe("Page not found · NetTriage");
});
```

- [ ] **Step 3: Run them and watch them fail**

Run: `cd frontend && pnpm test`
Expected: `3 failed | 3 passed` test files, with `15 passed` tests. The three new files fail before running, on `Failed to resolve import` for `./queryClient`, `../app/routes` (from `src/test/render.tsx`) and `./session`.

- [ ] **Step 4: Write the session, the server state and the routes**

`frontend/src/auth/session.ts`:
```ts
/** The signed-in person (spec §4.1, §6.2): who they are, and signing in and out. */
import { useQuery } from "@tanstack/react-query";
import { api, setCsrfToken } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Me = components["schemas"]["MeOut"];
export type Membership = components["schemas"]["MembershipOut"];

export const ME_KEY = ["me"] as const;

/** The signed-in person, or `null` when signed out. Keeps their CSRF token for the client. */
export async function fetchMe(): Promise<Me | null> {
  const result = await api.GET("/api/v1/me");
  if (result.response.status === 401) {
    setCsrfToken(null);
    return null;
  }
  const me = unwrap(result);
  setCsrfToken(me.csrf_token);
  return me;
}

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: 5 * 60_000 });
}

/** Signing in is a page load: the API sends the browser to Cognito's managed login. */
export function signInUrl(returnTo: string): string {
  return `/api/auth/login?return_to=${encodeURIComponent(returnTo)}`;
}

/** Where the browser goes next; tests replace `go`. */
export const browser = {
  go(url: string): void {
    window.location.assign(url);
  },
};

/** Ends this session, or every session the person has, then signs out of Cognito as well. */
export async function signOut({ everywhere }: { everywhere: boolean }): Promise<void> {
  const result = everywhere
    ? await api.POST("/api/auth/logout-all")
    : await api.POST("/api/auth/logout");
  const { logout_url } = unwrap(result);
  setCsrfToken(null);
  browser.go(logout_url);
}
```

`frontend/src/app/queryClient.ts`:
```ts
import { QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/problem";
import { ME_KEY } from "../auth/session";

/**
 * Server state for the app (TanStack Query). A failed read is retried only when a retry can
 * help: a network error or a 5xx, never a 4xx. A 401 means the session ended, so the person
 * is asked to sign in again.
 */
export function createQueryClient({ retries = 2 }: { retries?: number } = {}): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (error instanceof ApiError && error.status === 401 && query.queryKey[0] !== ME_KEY[0]) {
          void client.invalidateQueries({ queryKey: ME_KEY });
        }
      },
    }),
    defaultOptions: {
      queries: {
        retry: (failures, error) =>
          failures < retries && !(error instanceof ApiError && error.status < 500),
      },
      mutations: { retry: false },
    },
  });
  return client;
}
```

`frontend/src/ui/ExternalLink.tsx`:
```tsx
import type { ReactNode } from "react";

/** A link off the site: a new tab that can't reach back into the app (spec §10). */
export function ExternalLink({
  href,
  className,
  children,
}: {
  href: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <a href={href} className={className} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
```

`frontend/src/ui/usePageTitle.ts`:
```ts
import { useLayoutEffect } from "react";

/**
 * Names the page in the browser tab and for screen readers: "Members · NetTriage". Set as the
 * page is put on screen, so the title never lags behind the content.
 */
export function usePageTitle(title: string | null): void {
  useLayoutEffect(() => {
    document.title = title === null ? "NetTriage" : `${title} · NetTriage`;
  }, [title]);
}
```

`frontend/src/pages/Landing.tsx`:
```tsx
import { Link, useSearchParams } from "react-router";
import { signInUrl, useMe } from "../auth/session";
import { ExternalLink } from "../ui/ExternalLink";
import { usePageTitle } from "../ui/usePageTitle";

export const SOURCE_URL = "https://github.com/khajdar1/nettriage";

// The API sends a failed sign-in back here as `/?sign_in=<reason>` (spec §4.1, §6.5).
const SIGN_IN_PROBLEMS: Record<string, string> = {
  expired: "Your sign-in took too long, or started in another browser. Please sign in again.",
  failed: "Sign-in was cancelled or didn't finish. Please try again.",
  disabled: "This account is disabled.",
  unavailable: "Sign-in isn't available right now. Please try again in a few minutes.",
  limited: "Too many sign-in attempts. Please wait a minute, then try again.",
};

const FEATURES = [
  {
    title: "Detect",
    text: "Rules find port scans, SSH and RDP brute force, and unusual outbound volume in AWS VPC Flow Logs.",
  },
  {
    title: "Explain",
    text: "An AI explains each finding from its data alone, checked against that data and labeled as AI-generated.",
  },
  {
    title: "Triage",
    text: "Your team sets status and owner and adds comments, and nobody silently overwrites anybody else.",
  },
];

export function Landing() {
  usePageTitle(null);
  const [params] = useSearchParams();
  const problem = SIGN_IN_PROBLEMS[params.get("sign_in") ?? ""];
  const me = useMe();
  return (
    <main className="landing">
      <section className="hero">
        <p className="eyebrow">Network threat triage</p>
        <h1>NetTriage</h1>
        <p className="lede">
          Upload AWS VPC Flow Logs and get findings you can act on: detected by rules, explained by
          AI, triaged by your team.
        </p>
        {problem !== undefined && (
          <p role="alert" className="notice notice-error">
            {problem}
          </p>
        )}
        <div className="actions">
          {me.data ? (
            <Link className="button button-primary" to="/app">
              Open your organizations
            </Link>
          ) : (
            <a className="button button-primary" href={signInUrl("/app")}>
              Sign in / Sign up
            </a>
          )}
          <ExternalLink className="button" href={SOURCE_URL}>
            Source on GitHub
          </ExternalLink>
        </div>
      </section>
      <section className="features" aria-label="What it does">
        {FEATURES.map((feature) => (
          <article key={feature.title} className="feature">
            <h2>{feature.title}</h2>
            <p>{feature.text}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
```

`frontend/src/pages/NotFound.tsx`:
```tsx
import { Link } from "react-router";
import { usePageTitle } from "../ui/usePageTitle";

export function NotFound() {
  usePageTitle("Page not found");
  return (
    <main className="page narrow">
      <h1>Page not found</h1>
      <p>There's nothing at this address.</p>
      <Link to="/">Go to the home page</Link>
    </main>
  );
}
```

`frontend/src/app/routes.tsx`:
```tsx
import type { RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";

/** Every page of the app (spec §10). CloudFront serves index.html for each of these paths. */
export const routes: RouteObject[] = [
  { path: "/", element: <Landing /> },
  { path: "*", element: <NotFound /> },
];
```

`frontend/src/main.tsx`:
```tsx
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/space-grotesk/latin-600.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { createQueryClient } from "./app/queryClient";
import { routes } from "./app/routes";
import "./styles.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("#root element missing");
}
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={createBrowserRouter(routes)} />
    </QueryClientProvider>
  </StrictMode>,
);
```

- [ ] **Step 5: The look, and the lint rules that keep the CSP whole**

`frontend/src/styles.css`:
```css
/*
 * NetTriage's look matches the directors' deck (the owner's decision, Plan 6a): navy, teal and
 * off-white, Space Grotesk for headings and IBM Plex Sans for text. The fonts are served from
 * the app itself (@fontsource), because the CSP allows no other origin (spec §6.7). Every style
 * lives in this file: the CSP allows no inline styles.
 */

:root {
  --navy: #0f1b2a;
  --navy-raised: #16263a;
  --navy-line: #2a3b52;
  --teal: #5cc8c0;
  --teal-dark: #0b6e6e;
  --teal-darker: #085858;
  --paper: #f4f5f1;
  --card: #fbfbf8;
  --line: #d9ddd5;
  --ink: #0f1b2a;
  --text: #3a4658;
  --muted: #5b6779;
  --danger: #b42318;
  --danger-dark: #8f1c13;
  --danger-tint: #fdecea;
  --ok-tint: #e6f4f1;
  --font-display: "Space Grotesk", system-ui, sans-serif;
  --font-text: "IBM Plex Sans", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --radius: 12px;
  font-family: var(--font-text);
  font-size: 16px;
  line-height: 1.5;
  color: var(--text);
  background: var(--paper);
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

body {
  margin: 0;
}

h1,
h2,
h3 {
  font-family: var(--font-display);
  font-weight: 600;
  line-height: 1.15;
  letter-spacing: -0.01em;
  color: var(--ink);
}

a {
  color: var(--teal-dark);
}

code,
.mono {
  font-family: var(--font-mono);
  font-size: 0.92em;
}

:focus-visible {
  outline: 3px solid var(--teal-dark);
  outline-offset: 2px;
}

.eyebrow {
  margin: 0;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--teal-dark);
}

.muted {
  color: var(--muted);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

/* Buttons and links that look like buttons */

.button {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.55rem 1.05rem;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  color: var(--ink);
  font: inherit;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
}

.button:hover {
  border-color: var(--teal-dark);
}

.button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.button-primary {
  border-color: var(--teal-dark);
  background: var(--teal-dark);
  color: #fff;
}

.button-primary:hover {
  background: var(--teal-darker);
}

.button-danger {
  border-color: var(--danger);
  background: var(--danger);
  color: #fff;
}

.button-danger:hover {
  border-color: var(--danger-dark);
  background: var(--danger-dark);
}

.button-quiet {
  border-color: transparent;
  background: transparent;
  color: var(--teal-dark);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}

/* Messages */

.notice {
  margin: 1rem 0;
  padding: 0.75rem 1rem;
  border: 1px solid;
  border-radius: 8px;
}

.notice-error {
  border-color: #f2b8b2;
  background: var(--danger-tint);
  color: #6f160f;
}

.notice-ok {
  border-color: #b5ddd6;
  background: var(--ok-tint);
  color: #084646;
}

/* Pages, cards, forms and tables */

.page {
  max-width: 64rem;
  margin: 0 auto;
  padding: 2.5rem 1.5rem 4rem;
}

.narrow {
  max-width: 36rem;
}

.card {
  margin: 1.5rem 0;
  padding: 1.5rem;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--card);
}

.card h2 {
  margin-top: 0;
  font-size: 1.25rem;
}

.card-danger {
  border-color: #f2b8b2;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin-bottom: 1rem;
}

.field label {
  font-weight: 600;
  color: var(--ink);
}

.field input,
.field select {
  padding: 0.55rem 0.7rem;
  border: 1px solid #b9c0b4;
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  font: inherit;
}

.field .hint {
  font-size: 0.9rem;
  color: var(--muted);
}

.inline-form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem;
}

.inline-form .field {
  margin-bottom: 0;
}

.table {
  width: 100%;
  border-collapse: collapse;
}

.table th,
.table td {
  padding: 0.65rem 0.5rem;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: middle;
}

.table th {
  font-size: 0.85rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}

.badge {
  display: inline-block;
  padding: 0.1rem 0.55rem;
  border-radius: 999px;
  background: #e3e7df;
  color: var(--ink);
  font-size: 0.85rem;
  font-weight: 600;
}

/* The landing page */

.landing {
  min-height: 100vh;
  background: var(--navy);
  color: #c9d2dc;
}

.landing .hero,
.landing .features {
  max-width: 64rem;
  margin: 0 auto;
  padding: 0 1.5rem;
}

.landing .hero {
  padding-top: 16vh;
  padding-bottom: 4rem;
}

.landing .eyebrow {
  color: var(--teal);
}

.landing h1 {
  margin: 0.5rem 0 1rem;
  font-size: clamp(3rem, 9vw, 5.5rem);
  color: var(--paper);
}

.landing .lede {
  max-width: 40rem;
  font-size: 1.3rem;
}

.landing .actions {
  margin-top: 2rem;
}

.landing .button {
  border-color: var(--navy-line);
  background: transparent;
  color: var(--paper);
}

.landing .button-primary {
  border-color: var(--teal);
  background: var(--teal);
  color: var(--navy);
}

.landing .features {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1.5rem;
  padding-bottom: 6rem;
}

.landing .feature {
  padding: 1.5rem;
  border: 1px solid var(--navy-line);
  border-radius: var(--radius);
  background: var(--navy-raised);
}

.landing .feature h2 {
  margin-top: 0;
  font-size: 1.3rem;
  color: var(--paper);
}
```

In `frontend/eslint.config.js`, replace:
```js
  { files: ["src/**/*.{ts,tsx}"], languageOptions: { globals: globals.browser } },
  { files: ["scripts/**/*.mjs", "*.config.{js,ts}"], languageOptions: { globals: globals.node } },
```
with:
```js
  { files: ["src/**/*.{ts,tsx}"], languageOptions: { globals: globals.browser } },
  {
    // The CSP allows no inline styles, and the app never renders HTML it was given (spec §10).
    files: ["src/**/*.tsx"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='style']",
          message: "The CSP allows no inline styles: add a class to styles.css.",
        },
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Render text, never HTML (spec §10).",
        },
      ],
    },
  },
  { files: ["scripts/**/*.mjs", "*.config.{js,ts}"], languageOptions: { globals: globals.node } },
```

- [ ] **Step 6: Remove the placeholder page**

Run: `git rm -q frontend/src/App.tsx frontend/src/App.test.tsx`

- [ ] **Step 7: Run the checks**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test && pnpm build && pnpm check:csp`
Expected:
- lint and types are clean, and `31 passed`;
- the build lists the four fonts' `.woff2` files under `dist/assets`;
- `dist/index.html: no inline scripts, styles or event handlers`.

- [ ] **Step 8: Commit**

```bash
git add frontend
git commit -m "feat(web): the app shell: routes, server state, the session, the landing page and the deck's look" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The signed-in frame, your organizations and account settings

**Files:**
- Create:
  - `frontend/src/app/RequireSession.tsx`, `frontend/src/app/AppLayout.tsx`;
  - `frontend/src/pages/Orgs.tsx`, `frontend/src/pages/Settings.tsx`;
  - `frontend/src/orgs/roles.ts`, `frontend/src/ui/ErrorNotice.tsx`, `frontend/src/ui/Loading.tsx`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`, `frontend/vite.config.ts`
- Test: `frontend/src/pages/Orgs.test.tsx`, `frontend/src/pages/Settings.test.tsx`

**Interfaces:**
- Consumes: Task 3's `useMe`, `ME_KEY`, `signInUrl`, `signOut`, `browser`, `usePageTitle`, `renderAt`, `ME` and `SIGNED_OUT`; Task 2's `api`, `idempotencyKey`, `unwrap` and `errorMessage`.
- Produces:
  - `RequireSession` (a loading state, then an error, the "Sign in to continue" page, or the children);
  - `AppLayout` (the skip link, the top bar with **Account settings** and **Sign out**, and an `<Outlet />`);
  - the `/app` routes, with `Orgs` as the index and `Settings` at `settings`;
  - `Role`, `ROLE_LABELS` and `roleLabel(role)` in `src/orgs/roles.ts`;
  - `ErrorNotice({ error })` (`role="alert"`) and `Loading` (`role="status"`);
  - `MAX_ORGS = 3`;
  - in the fixtures: `ORG_ID`, `ACME` (a membership) and `memberOf(...memberships)`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/fixtures.ts`, replace:
```ts
/** Data the fake API answers with, typed by the API's own schema. */
import type { Me } from "../auth/session";
import type { Reply } from "./fakeApi";

export const SIGNED_OUT: Reply = {
```
with:
```ts
/** Data the fake API answers with, typed by the API's own schema. */
import type { Me, Membership } from "../auth/session";
import type { Reply } from "./fakeApi";

export const SIGNED_OUT: Reply = {
```

In `frontend/src/test/fixtures.ts`, replace:
```ts
  csrf_token: "csrf-1",
};

```
with:
```ts
  csrf_token: "csrf-1",
};

export const ORG_ID = "01a10333-a115-741b-91ff-41d6e310d817";

export const ACME: Membership = {
  org_id: ORG_ID,
  name: "Acme Security",
  slug: "acme-security",
  role: "owner",
};

export function memberOf(...memberships: Membership[]): Me {
  return { ...ME, memberships };
}

```

In `frontend/vite.config.ts`, replace:
```ts
    unstubGlobals: true,
    // Vitest's default include glob also matches scripts/check-csp.test.mjs,
```
with:
```ts
    unstubGlobals: true,
    restoreMocks: true,
    // Vitest's default include glob also matches scripts/check-csp.test.mjs,
```

`frontend/src/pages/Orgs.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { ACME, ME, ORG_ID, SIGNED_OUT, memberOf } from "../test/fixtures";
import { renderAt } from "../test/render";

const CREATED = {
  id: ORG_ID,
  name: "Acme Security",
  slug: "acme-security",
  role: "owner",
  member_count: 1,
  created_at: "2026-10-03T19:17:58Z",
};

test("signed out, the app asks to sign in and comes back to the same page", async () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/app/settings");

  expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
    "href",
    "/api/auth/login?return_to=%2Fapp%2Fsettings",
  );
});

test("someone new is shown how to start", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/app");

  expect(await screen.findByRole("heading", { name: "Your organizations" })).toBeInTheDocument();
  expect(
    screen.getByText(
      "Create your first organization, or open an invitation link someone sent you.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Organization name" })).toBeInTheDocument();
});

test("each organization is listed with the person's role in it", async () => {
  const lab = {
    ...ACME,
    org_id: "01a10333-0000-7000-8000-000000000002",
    name: "Lab",
    role: "viewer",
  };
  fakeApi({ "GET /api/v1/me": { body: memberOf(ACME, lab) } });

  renderAt("/app");

  expect(await screen.findByRole("link", { name: "Acme Security" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}`,
  );
  expect(screen.getByRole("link", { name: "Lab" })).toBeInTheDocument();
  expect(screen.getByText("Owner")).toBeInTheDocument();
  expect(screen.getByText("Viewer")).toBeInTheDocument();
});

test("creating an organization sends its name once, then opens it", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/v1/orgs": { status: 201, body: CREATED },
  });
  const { user, router } = renderAt("/app");

  await user.type(
    await screen.findByRole("textbox", { name: "Organization name" }),
    "  Acme Security ",
  );
  await user.click(screen.getByRole("button", { name: "Create organization" }));

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.json()).toEqual({ name: "Acme Security" });
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
});

test("a create the API refuses says why", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/v1/orgs": {
      status: 409,
      body: {
        title: "Conflict",
        detail: "You can belong to at most 3 organizations.",
        trace_id: "abc123",
      },
    },
  });
  const { user } = renderAt("/app");

  await user.type(await screen.findByRole("textbox", { name: "Organization name" }), "Fourth");
  await user.click(screen.getByRole("button", { name: "Create organization" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You can belong to at most 3 organizations. (reference abc123)",
  );
});

test("at three organizations there's no way to create a fourth", async () => {
  const orgs = ["1", "2", "3"].map((n) => ({
    ...ACME,
    org_id: `01a10333-0000-7000-8000-00000000000${n}`,
    name: `Org ${n}`,
  }));
  fakeApi({ "GET /api/v1/me": { body: memberOf(...orgs) } });

  renderAt("/app");

  expect(
    await screen.findByText("You belong to 3 organizations, the most there can be."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Organization name" })).not.toBeInTheDocument();
});
```

`frontend/src/pages/Settings.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { browser } from "../auth/session";
import { fakeApi } from "../test/fakeApi";
import { ME } from "../test/fixtures";
import { renderAt } from "../test/render";

const LOGOUT_URL = "https://auth.example.com/logout?client_id=abc";

test("settings show who is signed in", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/app/settings");

  expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  expect(screen.getAllByText("ana@example.com").length).toBeGreaterThan(0);
  expect(document.title).toBe("Settings · NetTriage");
});

test("sign out everywhere ends every session, then Cognito's", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/auth/logout-all": { body: { logout_url: LOGOUT_URL } },
  });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  const { user } = renderAt("/app/settings");

  await user.click(await screen.findByRole("button", { name: "Sign out everywhere" }));

  await vi.waitFor(() => expect(go).toHaveBeenCalledWith(LOGOUT_URL));
  expect(fake.requests.some((request) => request.url.endsWith("/api/auth/logout-all"))).toBe(true);
});

test("sign out, on every page, ends this session", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/auth/logout": { body: { logout_url: LOGOUT_URL } },
  });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  const { user } = renderAt("/app");

  await user.click(await screen.findByRole("button", { name: "Sign out" }));

  await vi.waitFor(() => expect(go).toHaveBeenCalledWith(LOGOUT_URL));
});

test("a sign-out that fails says why and keeps the person here", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/auth/logout-all": {
      status: 503,
      body: { title: "Service Unavailable", detail: "Signing out is unavailable.", trace_id: "t1" },
    },
  });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  const { user } = renderAt("/app/settings");

  await user.click(await screen.findByRole("button", { name: "Sign out everywhere" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Signing out is unavailable. (reference t1)",
  );
  expect(go).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && pnpm exec vitest run src/pages/Orgs.test.tsx src/pages/Settings.test.tsx`
Expected: `10 failed`: every new test. `/app` isn't a page yet, so each one misses what it looks for, such as `Unable to find role="heading" and name "Sign in to continue"`.

- [ ] **Step 3: Write the frame and the pages**

`frontend/src/orgs/roles.ts`:
```ts
import type { components } from "../api/schema";

export type Role = components["schemas"]["MemberOut"]["role"];

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  analyst: "Analyst",
  viewer: "Viewer",
};

export function roleLabel(role: string): string {
  return role in ROLE_LABELS ? ROLE_LABELS[role as Role] : role;
}
```

`frontend/src/ui/ErrorNotice.tsx`:
```tsx
import { errorMessage } from "../api/problem";

/** A failed request, said plainly, with the reference support needs (spec §10). */
export function ErrorNotice({ error }: { error: unknown }) {
  return (
    <p role="alert" className="notice notice-error">
      {errorMessage(error)}
    </p>
  );
}
```

`frontend/src/ui/Loading.tsx`:
```tsx
/** Shown while a page's data loads; screen readers announce it once. */
export function Loading() {
  return (
    <p role="status" className="muted">
      Loading…
    </p>
  );
}
```

`frontend/src/app/RequireSession.tsx`:
```tsx
import type { ReactNode } from "react";
import { useLocation } from "react-router";
import { signInUrl, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { Loading } from "../ui/Loading";
import { usePageTitle } from "../ui/usePageTitle";

function SignInPrompt({ returnTo }: { returnTo: string }) {
  usePageTitle("Sign in");
  return (
    <main id="main" className="page narrow">
      <h1>Sign in to continue</h1>
      <p>You're signed out, or your session ended.</p>
      <a className="button button-primary" href={signInUrl(returnTo)}>
        Sign in
      </a>
    </main>
  );
}

/** The signed-in part of the app: anyone else is asked to sign in, then brought back here. */
export function RequireSession({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) {
    return (
      <main id="main" className="page">
        <Loading />
      </main>
    );
  }
  if (me.isError) {
    return (
      <main id="main" className="page narrow">
        <ErrorNotice error={me.error} />
      </main>
    );
  }
  if (me.data === null) {
    return <SignInPrompt returnTo={location.pathname + location.search} />;
  }
  return children;
}
```

`frontend/src/app/AppLayout.tsx`:
```tsx
import { useMutation } from "@tanstack/react-query";
import { Link, Outlet } from "react-router";
import { signOut, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";

/** The frame of every signed-in page: the product, the person, and signing out. */
export function AppLayout() {
  const me = useMe();
  const leaving = useMutation({ mutationFn: () => signOut({ everywhere: false }) });
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link to="/app" className="brand">
          NetTriage
        </Link>
        <nav className="account" aria-label="Account">
          <span className="who">{me.data?.user.email}</span>
          <Link to="/app/settings">Settings</Link>
          <button
            type="button"
            className="button button-quiet"
            disabled={leaving.isPending}
            onClick={() => leaving.mutate()}
          >
            Sign out
          </button>
        </nav>
      </header>
      {leaving.isError && (
        <div className="page">
          <ErrorNotice error={leaving.error} />
        </div>
      )}
      <Outlet />
    </div>
  );
}
```

`frontend/src/pages/Orgs.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, idempotencyKey } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, useMe } from "../auth/session";
import { roleLabel } from "../orgs/roles";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";

/** A person belongs to at most three organizations (spec §2.1, §7). */
export const MAX_ORGS = 3;

function CreateOrg() {
  const [name, setName] = useState("");
  // One key per form: a resent request returns the org already created (spec §7).
  const [key] = useState(idempotencyKey);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: async (orgName: string) =>
      unwrap(
        await api.POST("/api/v1/orgs", {
          body: { name: orgName },
          headers: { "Idempotency-Key": key },
        }),
      ),
    onSuccess: async (org) => {
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
      await navigate(`/app/orgs/${org.id}`);
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate(name.trim());
  }
  return (
    <section className="card" aria-labelledby="create-org">
      <h2 id="create-org">Create an organization</h2>
      <form className="inline-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="org-name">Organization name</label>
          <input
            id="org-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={100}
          />
        </div>
        <button type="submit" className="button button-primary" disabled={create.isPending}>
          Create organization
        </button>
      </form>
      {create.isError && <ErrorNotice error={create.error} />}
    </section>
  );
}

/** `/app`: the person's organizations, and creating one (spec §10). */
export function Orgs() {
  usePageTitle("Your organizations");
  const me = useMe();
  const memberships = me.data?.memberships ?? [];
  return (
    <main id="main" className="page">
      <h1>Your organizations</h1>
      {memberships.length === 0 ? (
        <p>Create your first organization, or open an invitation link someone sent you.</p>
      ) : (
        <ul className="org-list">
          {memberships.map((membership) => (
            <li key={membership.org_id}>
              <Link to={`/app/orgs/${membership.org_id}`}>{membership.name}</Link>
              <span className="badge">{roleLabel(membership.role)}</span>
            </li>
          ))}
        </ul>
      )}
      {memberships.length < MAX_ORGS ? (
        <CreateOrg />
      ) : (
        <p className="muted">You belong to {MAX_ORGS} organizations, the most there can be.</p>
      )}
    </main>
  );
}
```

`frontend/src/pages/Settings.tsx`:
```tsx
import { useMutation } from "@tanstack/react-query";
import { signOut, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { usePageTitle } from "../ui/usePageTitle";

/** `/app/settings`: the account, and "sign out everywhere" (spec §2.2, §10). */
export function Settings() {
  usePageTitle("Settings");
  const me = useMe();
  const everywhere = useMutation({ mutationFn: () => signOut({ everywhere: true }) });
  return (
    <main id="main" className="page narrow">
      <h1>Settings</h1>
      <section className="card" aria-labelledby="account">
        <h2 id="account">Your account</h2>
        <p>
          Signed in as <strong>{me.data?.user.email}</strong>.
        </p>
      </section>
      <section className="card" aria-labelledby="everywhere">
        <h2 id="everywhere">Sign out everywhere</h2>
        <p>Ends your sessions in every browser and on every device, this one included.</p>
        <button
          type="button"
          className="button button-danger"
          disabled={everywhere.isPending}
          onClick={() => everywhere.mutate()}
        >
          Sign out everywhere
        </button>
        {everywhere.isError && <ErrorNotice error={everywhere.error} />}
      </section>
    </main>
  );
}
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { NotFound } from "../pages/NotFound";

```
with:
```tsx
import { NotFound } from "../pages/NotFound";
import { Orgs } from "../pages/Orgs";
import { Settings } from "../pages/Settings";
import { AppLayout } from "./AppLayout";
import { RequireSession } from "./RequireSession";

```

In `frontend/src/app/routes.tsx`, replace:
```tsx
  { path: "/", element: <Landing /> },
  { path: "*", element: <NotFound /> },
```
with:
```tsx
  { path: "/", element: <Landing /> },
  {
    path: "/app",
    element: (
      <RequireSession>
        <AppLayout />
      </RequireSession>
    ),
    children: [
      { index: true, element: <Orgs /> },
      { path: "settings", element: <Settings /> },
    ],
  },
  { path: "*", element: <NotFound /> },
```

In `frontend/src/styles.css`, replace:
```css

/* The landing page */
```
with:
```css

/* The signed-in frame */

.skip-link {
  position: absolute;
  top: -3rem;
  left: 1rem;
  z-index: 10;
  padding: 0.5rem 1rem;
  border-radius: 8px;
  background: var(--teal);
  color: var(--navy);
  font-weight: 600;
}

.skip-link:focus {
  top: 0.5rem;
}

.topbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.75rem 1.5rem;
  background: var(--navy);
  color: #c9d2dc;
}

.topbar a {
  color: var(--paper);
}

.brand {
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 600;
  text-decoration: none;
}

.account {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1rem;
}

.account .button-quiet {
  color: var(--teal);
}

.org-list {
  display: grid;
  gap: 0.75rem;
  margin: 1.5rem 0;
  padding: 0;
  list-style: none;
}

.org-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.25rem;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--card);
}

.org-list a {
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
}

/* The landing page */
```

- [ ] **Step 4: Run the checks**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `41 passed`.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat(web): the signed-in frame, your organizations, creating one, and signing out everywhere" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: An organization's frame, members and invitations

**Files:**
- Create:
  - `frontend/src/orgs/permissions.ts`, `frontend/src/orgs/org.ts`, `frontend/src/ui/format.ts`;
  - `frontend/src/pages/org/OrgLayout.tsx`, `Members.tsx`, `MemberRow.tsx`, `Invitations.tsx`;
  - `frontend/src/test/orgApi.ts`
- Modify: `frontend/src/app/routes.tsx`, `frontend/src/styles.css`, `frontend/src/test/fixtures.ts`
- Test: `frontend/src/orgs/permissions.test.ts`, `frontend/src/pages/org/Members.test.tsx`

**Interfaces:**
- Consumes:
  - Task 4's `ErrorNotice`, `Loading`, `Role`, `ROLE_LABELS`, `roleLabel`, `ACME`, `ORG_ID` and `memberOf`;
  - Task 3's `useMe`, `usePageTitle` and `renderAt`; Task 2's `api` and `unwrap`.
- Produces:
  - in `src/orgs/permissions.ts`: `assignableRoles(role)`, `canInvite(role)`, `canManage(actor, member, isSelf)`, `canRename(role)` and `canDelete(role)`;
  - in `src/orgs/org.ts`: `Org`, `orgKey(orgId)`, `useOrgQuery(orgId)` and `useOrg()` (the frame's `Outlet` context);
  - `formatDate(iso)`;
  - `OrgLayout`, whose tabs Task 6 extends; `Member` and `membersKey(orgId)` from `MemberRow.tsx`;
  - the route `/app/orgs/:orgId`, which opens `members`;
  - for tests: `ORG` and `signedInAs(member, extra)` in `src/test/orgApi.ts`, and the fixtures `org(role)`, `OWNER`, `ADMIN` and `VIEWER`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/test/fixtures.ts`, replace:
```ts
/** Data the fake API answers with, typed by the API's own schema. */
import type { Me, Membership } from "../auth/session";
import type { Reply } from "./fakeApi";

```
with:
```ts
/** Data the fake API answers with, typed by the API's own schema. */
import type { Me, Membership } from "../auth/session";
import type { Org } from "../orgs/org";
import type { Member } from "../pages/org/MemberRow";
import type { Reply } from "./fakeApi";

```

In `frontend/src/test/fixtures.ts`, replace:
```ts
}

```
with:
```ts
}

export function org(role: Org["role"] = "owner"): Org {
  return {
    id: ORG_ID,
    name: "Acme Security",
    slug: "acme-security",
    role,
    member_count: 3,
    created_at: "2026-10-01T09:00:00Z",
  };
}

export const OWNER: Member = {
  user_id: ME.user.id,
  email: "ana@example.com",
  display_name: null,
  role: "owner",
  joined_at: "2026-10-01T09:00:00Z",
};

export const ADMIN: Member = {
  user_id: "01a0e9e9-0000-7000-8000-00000000000a",
  email: "ben@example.com",
  display_name: "Ben Admin",
  role: "admin",
  joined_at: "2026-10-02T09:00:00Z",
};

export const VIEWER: Member = {
  user_id: "01a0e9e9-0000-7000-8000-00000000000b",
  email: "cleo@example.com",
  display_name: null,
  role: "viewer",
  joined_at: "2026-10-02T10:00:00Z",
};

```

`frontend/src/test/orgApi.ts`:
```ts
/** The fake API for a person signed in to Acme Security as one of its members. */
import type { Me } from "../auth/session";
import type { Member } from "../pages/org/MemberRow";
import { type FakeApi, type Handler, fakeApi } from "./fakeApi";
import { ACME, ADMIN, ME, ORG_ID, OWNER, VIEWER, memberOf, org } from "./fixtures";

export const ORG = `/api/v1/orgs/${ORG_ID}`;

export function signedInAs(self: Member, extra: Record<string, Handler> = {}): FakeApi {
  const me: Me = {
    ...memberOf({ ...ACME, role: self.role }),
    user: { ...ME.user, id: self.user_id, email: self.email },
  };
  return fakeApi({
    "GET /api/v1/me": { body: me },
    [`GET ${ORG}`]: { body: org(self.role) },
    [`GET ${ORG}/members`]: { body: { members: [OWNER, ADMIN, VIEWER] } },
    [`GET ${ORG}/invitations`]: { body: { invitations: [] } },
    ...extra,
  });
}
```

`frontend/src/orgs/permissions.test.ts`:
```ts
import { expect, test } from "vitest";
import { assignableRoles, canDelete, canInvite, canManage, canRename } from "./permissions";
import type { Role } from "./roles";

const ROLES: Role[] = ["owner", "admin", "analyst", "viewer"];

test("an owner gives any role, an admin only analyst or viewer, and no one else any", () => {
  expect(ROLES.map(assignableRoles)).toEqual([
    ["owner", "admin", "analyst", "viewer"],
    ["analyst", "viewer"],
    [],
    [],
  ]);
  expect(ROLES.map(canInvite)).toEqual([true, true, false, false]);
});

test("an admin can't change an owner or another admin", () => {
  expect(canManage("admin", "owner", false)).toBe(false);
  expect(canManage("admin", "admin", false)).toBe(false);
  expect(canManage("admin", "viewer", false)).toBe(true);
});

test("no one changes or removes themselves: they leave instead", () => {
  expect(canManage("owner", "owner", true)).toBe(false);
});

test("owners and admins rename the organization, and only owners delete it", () => {
  expect(ROLES.map(canRename)).toEqual([true, true, false, false]);
  expect(ROLES.map(canDelete)).toEqual([true, false, false, false]);
});
```

`frontend/src/pages/org/Members.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Role } from "../../orgs/roles";
import { ACME, ADMIN, ORG_ID, OWNER, SIGNED_OUT, VIEWER, memberOf } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";
import { formatDate } from "../../ui/format";

const INVITED = {
  id: "01a10400-0000-7000-8000-000000000001",
  email: "dan@example.com",
  role: "analyst" as const,
  expires_at: "2026-10-10T09:00:00Z",
  created_at: "2026-10-03T09:00:00Z",
  created_by: OWNER.user_id,
};

function row(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(name) });
}

test("an organization opens on its members, under its name and the person's role", async () => {
  signedInAs(OWNER);

  renderAt(`/app/orgs/${ORG_ID}`);

  expect(await screen.findByRole("heading", { level: 1, name: "Members" })).toBeInTheDocument();
  expect(screen.getByText("Acme Security", { selector: ".org-name" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Members · Acme Security · NetTriage");
});

test("an organization that isn't there, or isn't the person's, says so", async () => {
  signedInAs(OWNER, { [`GET ${ORG}`]: { status: 404, body: { title: "Not Found" } } });

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(
    await screen.findByRole("heading", { name: "Organization not found" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Your organizations" })).toHaveAttribute("href", "/app");
});

test("a session that ends during a visit asks to sign in again", async () => {
  let reads = 0;
  signedInAs(OWNER, {
    "GET /api/v1/me": () => (++reads === 1 ? { body: memberOf(ACME) } : SIGNED_OUT),
    [`GET ${ORG}`]: SIGNED_OUT,
  });

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
});

test("members are listed with their roles, and the person is marked", async () => {
  signedInAs(OWNER);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(await screen.findByText("Ben Admin")).toBeInTheDocument();
  expect(within(row("ana@example.com")).getByText("You")).toBeInTheDocument();
  expect(within(row("Ben Admin")).getByText("ben@example.com")).toBeInTheDocument();
  expect(within(row("cleo")).getByText(formatDate(VIEWER.joined_at))).toBeInTheDocument();
});

test("an owner changes a member's role", async () => {
  const fake = signedInAs(OWNER, {
    [`PATCH ${ORG}/members/${VIEWER.user_id}`]: { body: { ...VIEWER, role: "analyst" } },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Role of cleo@example.com" }),
    "Analyst",
  );

  await vi.waitFor(() => expect(fake.requests.some((r) => r.method === "PATCH")).toBe(true));
  const patch = fake.requests.find((request) => request.method === "PATCH");
  expect(await patch?.json()).toEqual({ role: "analyst" });
});

test("an admin changes only analysts and viewers, and never themselves", async () => {
  signedInAs(ADMIN);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  const viewerRole = await screen.findByRole("combobox", { name: "Role of cleo@example.com" });
  const offered = within(viewerRole)
    .getAllByRole("option")
    .map((option) => option.textContent);
  expect(offered).toEqual(["Analyst", "Viewer"]);
  expect(screen.queryByRole("combobox", { name: "Role of ana@example.com" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Role of Ben Admin" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove Ben Admin" })).toBeNull();
});

test("removing a member asks first", async () => {
  const fake = signedInAs(OWNER, { [`DELETE ${ORG}/members/${VIEWER.user_id}`]: { status: 204 } });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.click(await screen.findByRole("button", { name: "Remove cleo@example.com" }));
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(fake.requests.some((request) => request.method === "DELETE")).toBe(false);

  await user.click(screen.getByRole("button", { name: "Remove cleo@example.com" }));
  await user.click(screen.getByRole("button", { name: "Yes, remove" }));

  await vi.waitFor(() => expect(fake.requests.some((r) => r.method === "DELETE")).toBe(true));
});

test("a change the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`PATCH ${ORG}/members/${VIEWER.user_id}`]: {
      status: 403,
      body: { title: "Forbidden", detail: "You can't give a role above your own.", trace_id: "t9" },
    },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Role of cleo@example.com" }),
    "Owner",
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You can't give a role above your own. (reference t9)",
  );
});

test.each<Role>(["analyst", "viewer"])(
  "an %s sees the members and nothing to change",
  async (role) => {
    signedInAs({ ...VIEWER, role });

    renderAt(`/app/orgs/${ORG_ID}/members`);

    expect(await screen.findByText("Ben Admin")).toBeInTheDocument();
    expect(screen.queryAllByRole("combobox")).toEqual([]);
    expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Invitations" })).toBeNull();
  },
);

test("inviting someone shows their link once, ready to copy", async () => {
  let invitations: (typeof INVITED)[] = [];
  const url = "https://d111111abcdef8.cloudfront.net/invite#tok3n";
  const fake = signedInAs(OWNER, {
    [`GET ${ORG}/invitations`]: () => ({ body: { invitations } }),
    [`POST ${ORG}/invitations`]: () => {
      invitations = [INVITED];
      return { status: 201, body: { invitation: INVITED, invite_url: url } };
    },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.type(
    await screen.findByRole("textbox", { name: "Email address" }),
    " dan@example.com ",
  );
  await user.selectOptions(screen.getByRole("combobox", { name: "Role" }), "Analyst");
  await user.click(screen.getByRole("button", { name: "Invite" }));

  expect(await screen.findByRole("textbox", { name: "Invitation link" })).toHaveValue(url);
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.json()).toEqual({ email: "dan@example.com", role: "analyst" });
  expect(await screen.findByRole("cell", { name: "dan@example.com" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Copy link" }));
  expect(await navigator.clipboard.readText()).toBe(url);
  expect(screen.getByText("Copied.")).toBeInTheDocument();
});

test("an admin invites only analysts and viewers", async () => {
  signedInAs(ADMIN);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  const roles = await screen.findByRole("combobox", { name: "Role" });
  expect(
    within(roles)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Analyst", "Viewer"]);
});

test("a pending invitation shows when it expires, and can be revoked", async () => {
  const fake = signedInAs(OWNER, {
    [`GET ${ORG}/invitations`]: { body: { invitations: [INVITED] } },
    [`DELETE ${ORG}/invitations/${INVITED.id}`]: { status: 204 },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(
    await screen.findByRole("cell", { name: formatDate(INVITED.expires_at) }),
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Revoke the invitation for dan@example.com" }),
  );

  await vi.waitFor(() => expect(fake.requests.some((r) => r.method === "DELETE")).toBe(true));
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && pnpm exec vitest run src/orgs/permissions.test.ts src/pages/org/Members.test.tsx`
Expected: Both files fail before any test runs: `Failed to resolve import "./permissions"` and `Failed to resolve import "../../ui/format"`.

- [ ] **Step 3: Write the permissions and the organization's frame**

`frontend/src/orgs/permissions.ts`:
```ts
/**
 * What a role may do in an organization (spec §6.4), so the app offers only what will work.
 * The API decides: these only hide controls that would be refused.
 */
import type { Role } from "./roles";

const MANAGED_BY: Record<Role, Role[]> = {
  owner: ["owner", "admin", "analyst", "viewer"],
  admin: ["analyst", "viewer"],
  analyst: [],
  viewer: [],
};

/** The roles someone with `role` may give, and the members they may change or remove. */
export function assignableRoles(role: Role): Role[] {
  return MANAGED_BY[role];
}

export function canInvite(role: Role): boolean {
  return assignableRoles(role).length > 0;
}

/** May `actor` change this member's role or remove them? Never their own: they leave instead. */
export function canManage(actor: Role, member: Role, isSelf: boolean): boolean {
  return !isSelf && assignableRoles(actor).includes(member);
}

export function canRename(role: Role): boolean {
  return role === "owner" || role === "admin";
}

export function canDelete(role: Role): boolean {
  return role === "owner";
}
```

`frontend/src/orgs/org.ts`:
```ts
/** The organization a page belongs to (`/app/orgs/:orgId/…`), loaded once by its frame. */
import { useQuery } from "@tanstack/react-query";
import { useOutletContext } from "react-router";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Org = components["schemas"]["OrgOut"];

export function orgKey(orgId: string) {
  return ["org", orgId] as const;
}

export function useOrgQuery(orgId: string) {
  return useQuery({
    queryKey: orgKey(orgId),
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/orgs/{org_id}", { params: { path: { org_id: orgId } } })),
  });
}

/** The current organization, for the pages inside its frame. */
export function useOrg(): Org {
  return useOutletContext<Org>();
}
```

`frontend/src/ui/format.ts`:
```ts
const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

/** A date as the person's browser writes dates, such as "3 Oct 2026". */
export function formatDate(iso: string): string {
  return DATE.format(new Date(iso));
}
```

`frontend/src/pages/org/OrgLayout.tsx`:
```tsx
import { Link, NavLink, Outlet, useParams } from "react-router";
import { ApiError } from "../../api/problem";
import { useOrgQuery } from "../../orgs/org";
import { roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";

function OrgNotFound() {
  usePageTitle("Organization not found");
  return (
    <main id="main" className="page narrow">
      <h1>Organization not found</h1>
      <p>It may have been deleted, or you're no longer a member.</p>
      <Link to="/app">Your organizations</Link>
    </main>
  );
}

/** The frame of an organization's pages: its name, the person's role in it, and its tabs. */
export function OrgLayout() {
  const { orgId = "" } = useParams();
  const org = useOrgQuery(orgId);
  if (org.isPending) {
    return (
      <main id="main" className="page">
        <Loading />
      </main>
    );
  }
  if (org.isError) {
    if (org.error instanceof ApiError && org.error.status === 404) {
      return <OrgNotFound />;
    }
    return (
      <main id="main" className="page">
        <ErrorNotice error={org.error} />
      </main>
    );
  }
  return (
    <>
      <div className="org-bar">
        <div className="org-bar-inner">
          <p className="org-name">
            {org.data.name}
            <span className="badge">{roleLabel(org.data.role)}</span>
          </p>
          <nav className="tabs" aria-label="Organization">
            <NavLink to="members">Members</NavLink>
          </nav>
        </div>
      </div>
      <Outlet context={org.data} />
    </>
  );
}
```

- [ ] **Step 4: Write the members page**

`frontend/src/pages/org/MemberRow.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import type { components } from "../../api/schema";
import type { Org } from "../../orgs/org";
import { assignableRoles, canManage } from "../../orgs/permissions";
import { ROLE_LABELS, type Role, roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDate } from "../../ui/format";

export type Member = components["schemas"]["MemberOut"];

export function membersKey(orgId: string) {
  return ["members", orgId] as const;
}

/** One member: their role, which an Owner or Admin may change, and removing them. */
export function MemberRow({ org, member, isSelf }: { org: Org; member: Member; isSelf: boolean }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const path = { org_id: org.id, user_id: member.user_id };
  const refresh = () => queryClient.invalidateQueries({ queryKey: membersKey(org.id) });
  const changeRole = useMutation({
    mutationFn: async (role: Role) =>
      unwrap(
        await api.PATCH("/api/v1/orgs/{org_id}/members/{user_id}", {
          params: { path },
          body: { role },
        }),
      ),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: async () =>
      unwrap(await api.DELETE("/api/v1/orgs/{org_id}/members/{user_id}", { params: { path } })),
    onSuccess: refresh,
  });
  const who = member.display_name ?? member.email;
  const manageable = canManage(org.role, member.role, isSelf);
  const error = changeRole.error ?? remove.error;
  return (
    <>
      <tr>
        <td>
          {who}
          {isSelf && <span className="badge">You</span>}
          {member.display_name !== null && <div className="muted">{member.email}</div>}
        </td>
        <td>
          {manageable ? (
            <select
              aria-label={`Role of ${who}`}
              value={member.role}
              disabled={changeRole.isPending}
              onChange={(event) => changeRole.mutate(event.target.value as Role)}
            >
              {assignableRoles(org.role).map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          ) : (
            roleLabel(member.role)
          )}
        </td>
        <td>{formatDate(member.joined_at)}</td>
        <td>
          {manageable &&
            (confirming ? (
              <span className="actions">
                <button
                  type="button"
                  className="button button-danger"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate()}
                >
                  Yes, remove
                </button>
                <button type="button" className="button" onClick={() => setConfirming(false)}>
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="button"
                aria-label={`Remove ${who}`}
                onClick={() => setConfirming(true)}
              >
                Remove
              </button>
            ))}
        </td>
      </tr>
      {error !== null && (
        <tr>
          <td colSpan={4}>
            <ErrorNotice error={error} />
          </td>
        </tr>
      )}
    </>
  );
}
```

`frontend/src/pages/org/Invitations.tsx`:
```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import type { Org } from "../../orgs/org";
import { assignableRoles } from "../../orgs/permissions";
import { ROLE_LABELS, type Role, roleLabel } from "../../orgs/roles";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDate } from "../../ui/format";
import { Loading } from "../../ui/Loading";

function invitationsKey(orgId: string) {
  return ["invitations", orgId] as const;
}

/** The link to send, shown once: the API keeps only its hash (spec §6.3). */
function InviteLink({ url, email }: { url: string; email: string }) {
  const [copied, setCopied] = useState<boolean | null>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="invite-link">
      <p>
        Send this link to <strong>{email}</strong>. It works once, for 7 days, and only for that
        email address. It won't be shown again.
      </p>
      <div className="inline-form">
        <input
          className="mono"
          readOnly
          aria-label="Invitation link"
          value={url}
          onFocus={(event) => event.target.select()}
        />
        <button type="button" className="button" onClick={() => void copy()}>
          Copy link
        </button>
      </div>
      <p role="status" className="muted">
        {copied === true && "Copied."}
        {copied === false && "Couldn't copy: select the link and copy it yourself."}
      </p>
    </div>
  );
}

function InviteForm({ org }: { org: Org }) {
  const roles = assignableRoles(org.role);
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const invite = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/orgs/{org_id}/invitations", {
          params: { path: { org_id: org.id } },
          body: { email: email.trim(), role },
        }),
      ),
    onSuccess: async () => {
      setEmail("");
      await queryClient.invalidateQueries({ queryKey: invitationsKey(org.id) });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    invite.mutate();
  }
  return (
    <>
      <form className="inline-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="invite-email">Email address</label>
          <input
            id="invite-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            maxLength={320}
          />
        </div>
        <div className="field">
          <label htmlFor="invite-role">Role</label>
          <select
            id="invite-role"
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
          >
            {roles.map((option) => (
              <option key={option} value={option}>
                {ROLE_LABELS[option]}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="button button-primary" disabled={invite.isPending}>
          Invite
        </button>
      </form>
      {invite.isError && <ErrorNotice error={invite.error} />}
      {invite.data !== undefined && (
        <InviteLink url={invite.data.invite_url} email={invite.data.invitation.email} />
      )}
    </>
  );
}

/** Pending invitations, inviting someone, and revoking an invitation (spec §6.3, §7). */
export function Invitations({ org }: { org: Org }) {
  const queryClient = useQueryClient();
  const invitations = useQuery({
    queryKey: invitationsKey(org.id),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/invitations", {
          params: { path: { org_id: org.id } },
        }),
      ),
  });
  const revoke = useMutation({
    mutationFn: async (invitationId: string) =>
      unwrap(
        await api.DELETE("/api/v1/orgs/{org_id}/invitations/{invitation_id}", {
          params: { path: { org_id: org.id, invitation_id: invitationId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invitationsKey(org.id) }),
  });
  return (
    <section className="card" aria-labelledby="invitations">
      <h2 id="invitations">Invitations</h2>
      <InviteForm org={org} />
      {revoke.isError && <ErrorNotice error={revoke.error} />}
      {invitations.isPending && <Loading />}
      {invitations.isError && <ErrorNotice error={invitations.error} />}
      {invitations.data !== undefined &&
        (invitations.data.invitations.length === 0 ? (
          <p className="muted">No pending invitations.</p>
        ) : (
          <table className="table">
            <caption className="visually-hidden">Pending invitations</caption>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Expires</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {invitations.data.invitations.map((invitation) => (
                <tr key={invitation.id}>
                  <td>{invitation.email}</td>
                  <td>{roleLabel(invitation.role)}</td>
                  <td>{formatDate(invitation.expires_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="button"
                      aria-label={`Revoke the invitation for ${invitation.email}`}
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(invitation.id)}
                    >
                      Revoke
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
    </section>
  );
}
```

`frontend/src/pages/org/Members.tsx`:
```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { useMe } from "../../auth/session";
import { useOrg } from "../../orgs/org";
import { canInvite } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { Invitations } from "./Invitations";
import { MemberRow, membersKey } from "./MemberRow";

/** `/app/orgs/:org/members`: members, their roles, and invitations (spec §10). */
export function Members() {
  const org = useOrg();
  usePageTitle(`Members · ${org.name}`);
  const me = useMe();
  const members = useQuery({
    queryKey: membersKey(org.id),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: org.id } } }),
      ),
  });
  return (
    <main id="main" className="page">
      <h1>Members</h1>
      {members.isPending && <Loading />}
      {members.isError && <ErrorNotice error={members.error} />}
      {members.data !== undefined && (
        <table className="table">
          <caption className="visually-hidden">Members of {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Role</th>
              <th scope="col">Joined</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {members.data.members.map((member) => (
              <MemberRow
                key={member.user_id}
                org={org}
                member={member}
                isSelf={member.user_id === me.data?.user.id}
              />
            ))}
          </tbody>
        </table>
      )}
      {canInvite(org.role) && <Invitations org={org} />}
    </main>
  );
}
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
import type { RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { Orgs } from "../pages/Orgs";
```
with:
```tsx
import { Navigate, type RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
import { NotFound } from "../pages/NotFound";
import { Members } from "../pages/org/Members";
import { OrgLayout } from "../pages/org/OrgLayout";
import { Orgs } from "../pages/Orgs";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
      { path: "settings", element: <Settings /> },
    ],
```
with:
```tsx
      { path: "settings", element: <Settings /> },
      {
        path: "orgs/:orgId",
        element: <OrgLayout />,
        children: [
          { index: true, element: <Navigate to="members" replace /> },
          { path: "members", element: <Members /> },
        ],
      },
    ],
```

In `frontend/src/styles.css`, replace:
```css

/* The landing page */
```
with:
```css

/* An organization's frame */

.org-bar {
  border-bottom: 1px solid var(--line);
  background: var(--card);
}

.org-bar-inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  max-width: 64rem;
  margin: 0 auto;
  padding: 0.75rem 1.5rem;
}

.org-name {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 0;
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--ink);
}

.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.tabs a {
  padding: 0.4rem 0.8rem;
  border-radius: 8px;
  color: var(--text);
  font-weight: 600;
  text-decoration: none;
}

.tabs a:hover {
  background: #e9ece5;
}

.tabs a[aria-current="page"] {
  background: var(--teal-dark);
  color: #fff;
}

.invite-link {
  margin-top: 1.25rem;
  padding: 1rem;
  border: 1px solid #b5ddd6;
  border-radius: 8px;
  background: var(--ok-tint);
}

.invite-link input {
  flex: 1 1 24rem;
  padding: 0.55rem 0.7rem;
  border: 1px solid #b9c0b4;
  border-radius: 8px;
  background: #fff;
}

/* The landing page */
```

- [ ] **Step 5: Run the checks**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `58 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend
git commit -m "feat(web): an organization's frame and its members: roles, removal and invitations" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: An organization's settings

**Files:**
- Create: `frontend/src/pages/org/OrgSettings.tsx`
- Modify: `frontend/src/pages/org/OrgLayout.tsx`, `frontend/src/app/routes.tsx`, `frontend/src/app/AppLayout.tsx`, `frontend/src/pages/Settings.tsx`
- Test: `frontend/src/pages/org/OrgSettings.test.tsx`, `frontend/src/pages/Settings.test.tsx`

**Interfaces:**
- Consumes: Task 5's `useOrg`, `orgKey`, `canRename`, `canDelete`, `signedInAs`, `ORG`, `org`, `OWNER`, `ADMIN`, `VIEWER` and `Member`; Task 4's `ErrorNotice`; Task 3's `ME_KEY` and `useMe`.
- Produces:
  - `OrgSettings`, at `/app/orgs/:orgId/settings`, with a **Settings** tab in `OrgLayout`;
  - the top bar's account link, now **Account settings**, and the page heading and title "Account settings".

- [ ] **Step 1: Write the failing tests**

`frontend/src/pages/org/OrgSettings.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Member } from "./MemberRow";
import { ADMIN, ORG_ID, OWNER, VIEWER, org } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const SETTINGS = `/app/orgs/${ORG_ID}/settings`;

function sections(): string[] {
  return screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent ?? "");
}

test.each<[Member, string[]]>([
  [OWNER, ["Name", "Leave this organization", "Delete this organization"]],
  [ADMIN, ["Name", "Leave this organization"]],
  [VIEWER, ["Leave this organization"]],
])("a member sees only the settings their role allows", async (self, expected) => {
  signedInAs(self);

  renderAt(SETTINGS);

  expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  expect(sections()).toEqual(expected);
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
});

test("renaming saves the new name and shows it at once", async () => {
  const fake = signedInAs(OWNER, {
    [`PATCH ${ORG}`]: { body: { ...org(), name: "Acme" } },
  });
  const { user } = renderAt(SETTINGS);

  const name = await screen.findByRole("textbox", { name: "Organization name" });
  await user.clear(name);
  await user.type(name, " Acme ");
  await user.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("Saved.")).toBeInTheDocument();
  expect(screen.getByText("Acme", { selector: ".org-name" })).toBeInTheDocument();
  const patch = fake.requests.find((request) => request.method === "PATCH");
  expect(await patch?.json()).toEqual({ name: "Acme" });
});

test("a rename the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`PATCH ${ORG}`]: {
      status: 422,
      body: { title: "Unprocessable Content", detail: "The name can't be blank.", trace_id: "r1" },
    },
  });
  const { user } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Save" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The name can't be blank. (reference r1)",
  );
});

test("deleting needs the organization's exact name, then leaves for the organization list", async () => {
  const fake = signedInAs(OWNER, { [`DELETE ${ORG}`]: { status: 204 } });
  const { user, router } = renderAt(SETTINGS);

  const confirm = await screen.findByRole("textbox", { name: "Type Acme Security to confirm" });
  const button = screen.getByRole("button", { name: "Delete organization" });
  await user.type(confirm, "acme security");
  expect(button).toBeDisabled();
  await user.clear(confirm);
  await user.type(confirm, "Acme Security");
  await user.click(button);

  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  const deleted = fake.requests.find((request) => request.method === "DELETE");
  expect(deleted?.url).toBe(`http://localhost:3000${ORG}?confirm_name=Acme%20Security`);
});

test("leaving asks first, then leaves for the organization list", async () => {
  const fake = signedInAs(VIEWER, { [`DELETE ${ORG}/members/${VIEWER.user_id}`]: { status: 204 } });
  const { user, router } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Leave" }));
  await user.click(screen.getByRole("button", { name: "Yes, leave Acme Security" }));

  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  expect(fake.requests.some((request) => request.method === "DELETE")).toBe(true);
});

test("the last owner can't leave, and is told why", async () => {
  signedInAs(OWNER, {
    [`DELETE ${ORG}/members/${OWNER.user_id}`]: {
      status: 409,
      body: {
        title: "Conflict",
        detail: "An organization keeps at least one owner.",
        trace_id: "l1",
      },
    },
  });
  const { user, router } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Leave" }));
  await user.click(screen.getByRole("button", { name: "Yes, leave Acme Security" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "An organization keeps at least one owner. (reference l1)",
  );
  expect(router.state.location.pathname).toBe(SETTINGS);
});
```

In `frontend/src/pages/Settings.test.tsx`, replace:
```tsx

  expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  expect(screen.getAllByText("ana@example.com").length).toBeGreaterThan(0);
  expect(document.title).toBe("Settings · NetTriage");
});
```
with:
```tsx

  expect(
    await screen.findByRole("heading", { level: 1, name: "Account settings" }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("ana@example.com").length).toBeGreaterThan(0);
  expect(document.title).toBe("Account settings · NetTriage");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd frontend && pnpm exec vitest run src/pages/org/OrgSettings.test.tsx src/pages/Settings.test.tsx`
Expected: `9 failed | 3 passed`.
- Every organization settings test fails, because the page doesn't exist yet (for example `Unable to find role="heading" and name "Settings"`).
- So does `settings show who is signed in`, which now looks for **Account settings**.

- [ ] **Step 3: Write the settings page, its tab and its route**

`frontend/src/pages/org/OrgSettings.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { ME_KEY, useMe } from "../../auth/session";
import { type Org, orgKey, useOrg } from "../../orgs/org";
import { canDelete, canRename } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { usePageTitle } from "../../ui/usePageTitle";

function Rename({ org }: { org: Org }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(org.name);
  const rename = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PATCH("/api/v1/orgs/{org_id}", {
          params: { path: { org_id: org.id } },
          body: { name: name.trim() },
        }),
      ),
    onSuccess: async (renamed) => {
      queryClient.setQueryData(orgKey(org.id), renamed);
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    rename.mutate();
  }
  return (
    <section className="card" aria-labelledby="rename">
      <h2 id="rename">Name</h2>
      <form className="inline-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="org-rename">Organization name</label>
          <input
            id="org-rename"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={100}
          />
        </div>
        <button type="submit" className="button button-primary" disabled={rename.isPending}>
          Save
        </button>
      </form>
      <p role="status" className="muted">
        {rename.isSuccess && "Saved."}
      </p>
      {rename.isError && <ErrorNotice error={rename.error} />}
    </section>
  );
}

/** Leaving or deleting ends the person's access: back to their organizations. */
function useGone() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return async (orgId: string) => {
    queryClient.removeQueries({ queryKey: orgKey(orgId) });
    await queryClient.invalidateQueries({ queryKey: ME_KEY });
    await navigate("/app");
  };
}

function Leave({ org, userId }: { org: Org; userId: string }) {
  const gone = useGone();
  const [confirming, setConfirming] = useState(false);
  const leave = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.DELETE("/api/v1/orgs/{org_id}/members/{user_id}", {
          params: { path: { org_id: org.id, user_id: userId } },
        }),
      ),
    onSuccess: () => gone(org.id),
  });
  return (
    <section className="card" aria-labelledby="leave">
      <h2 id="leave">Leave this organization</h2>
      <p>You lose access to its uploads and findings until someone invites you again.</p>
      {confirming ? (
        <div className="actions">
          <button
            type="button"
            className="button button-danger"
            disabled={leave.isPending}
            onClick={() => leave.mutate()}
          >
            Yes, leave {org.name}
          </button>
          <button type="button" className="button" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <button type="button" className="button" onClick={() => setConfirming(true)}>
          Leave
        </button>
      )}
      {leave.isError && <ErrorNotice error={leave.error} />}
    </section>
  );
}

function Delete({ org }: { org: Org }) {
  const gone = useGone();
  const [typed, setTyped] = useState("");
  const remove = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.DELETE("/api/v1/orgs/{org_id}", {
          params: { path: { org_id: org.id }, query: { confirm_name: typed } },
        }),
      ),
    onSuccess: () => gone(org.id),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    remove.mutate();
  }
  return (
    <section className="card card-danger" aria-labelledby="delete">
      <h2 id="delete">Delete this organization</h2>
      <p>
        This deletes its members, invitations, uploads, findings and AI explanations. It can't be
        undone.
      </p>
      <form className="inline-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor="org-confirm">Type {org.name} to confirm</label>
          <input
            id="org-confirm"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
          />
        </div>
        <button
          type="submit"
          className="button button-danger"
          disabled={typed !== org.name || remove.isPending}
        >
          Delete organization
        </button>
      </form>
      {remove.isError && <ErrorNotice error={remove.error} />}
    </section>
  );
}

/** `/app/orgs/:org/settings`: rename, leave or delete the organization (spec §2.2). */
export function OrgSettings() {
  const org = useOrg();
  usePageTitle(`Settings · ${org.name}`);
  const me = useMe();
  return (
    <main id="main" className="page narrow">
      <h1>Settings</h1>
      {canRename(org.role) && <Rename org={org} />}
      {me.data && <Leave org={org} userId={me.data.user.id} />}
      {canDelete(org.role) && <Delete org={org} />}
    </main>
  );
}
```

In `frontend/src/pages/org/OrgLayout.tsx`, replace:
```tsx
            <NavLink to="members">Members</NavLink>
          </nav>
```
with:
```tsx
            <NavLink to="members">Members</NavLink>
            <NavLink to="settings">Settings</NavLink>
          </nav>
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { OrgLayout } from "../pages/org/OrgLayout";
import { Orgs } from "../pages/Orgs";
```
with:
```tsx
import { OrgLayout } from "../pages/org/OrgLayout";
import { OrgSettings } from "../pages/org/OrgSettings";
import { Orgs } from "../pages/Orgs";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
          { path: "members", element: <Members /> },
        ],
```
with:
```tsx
          { path: "members", element: <Members /> },
          { path: "settings", element: <OrgSettings /> },
        ],
```

- [ ] **Step 4: Tell the account's settings apart from the organization's**

In `frontend/src/app/AppLayout.tsx`, replace:
```tsx
          <span className="who">{me.data?.user.email}</span>
          <Link to="/app/settings">Settings</Link>
          <button
```
with:
```tsx
          <span className="who">{me.data?.user.email}</span>
          <Link to="/app/settings">Account settings</Link>
          <button
```

In `frontend/src/pages/Settings.tsx`, replace:
```tsx
export function Settings() {
  usePageTitle("Settings");
  const me = useMe();
```
with:
```tsx
export function Settings() {
  usePageTitle("Account settings");
  const me = useMe();
```

In `frontend/src/pages/Settings.tsx`, replace:
```tsx
    <main id="main" className="page narrow">
      <h1>Settings</h1>
      <section className="card" aria-labelledby="account">
```
with:
```tsx
    <main id="main" className="page narrow">
      <h1>Account settings</h1>
      <section className="card" aria-labelledby="account">
```

- [ ] **Step 5: Run the checks**

Run: `cd frontend && pnpm lint && pnpm exec tsc --noEmit && pnpm test`
Expected: lint and types are clean, and `66 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend
git commit -m "feat(web): an organization's settings: rename it, leave it, or delete it by its name" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Accepting an invitation

**Files:**
- Create: `frontend/src/pages/Invite.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Test: `frontend/src/pages/Invite.test.tsx`

**Interfaces:**
- Consumes: Task 3's `useMe`, `ME_KEY`, `signInUrl`, `usePageTitle` and `renderAt`; Task 4's `ErrorNotice` and `Loading`; Task 5's `org(role)` fixture; Task 2's `api` and `unwrap`.
- Produces: `Invite`, at `/invite`. The token waits in `sessionStorage` under `nettriage.invitation` until it's accepted.

- [ ] **Step 1: Write the failing test**

`frontend/src/pages/Invite.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { ME, ORG_ID, SIGNED_OUT, org } from "../test/fixtures";
import { renderAt } from "../test/render";

const STORED = "nettriage.invitation";
const ACCEPT = "POST /api/v1/invitations/accept";

afterEach(() => sessionStorage.clear());

test("a link without its code says so", () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/invite");

  expect(screen.getByRole("heading", { name: "Invitation link incomplete" })).toBeInTheDocument();
});

test("signed out, it keeps the code, clears the address bar and asks to sign in", async () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  const { router } = renderAt("/invite#tok3n");

  expect(await screen.findByRole("link", { name: "Sign in to accept" })).toHaveAttribute(
    "href",
    "/api/auth/login?return_to=%2Finvite",
  );
  expect(sessionStorage.getItem(STORED)).toBe("tok3n");
  await vi.waitFor(() => expect(router.state.location.hash).toBe(""));
});

test("back from signing in, it accepts with the kept code and opens the organization", async () => {
  sessionStorage.setItem(STORED, "tok3n");
  const fake = fakeApi({ "GET /api/v1/me": { body: ME }, [ACCEPT]: { body: org("analyst") } });

  const { router } = renderAt("/invite");

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  const accepted = fake.requests.filter((request) => request.method === "POST");
  expect(accepted).toHaveLength(1);
  expect(await accepted[0]?.json()).toEqual({ token: "tok3n" });
  expect(sessionStorage.getItem(STORED)).toBeNull();
});

test("a link opened while signed in is accepted once, right away", async () => {
  const fake = fakeApi({ "GET /api/v1/me": { body: ME }, [ACCEPT]: { body: org("viewer") } });

  const { router } = renderAt("/invite#tok3n");

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  expect(fake.requests.filter((request) => request.method === "POST")).toHaveLength(1);
});

test("an invitation that can't be used says why", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    [ACCEPT]: {
      status: 409,
      body: {
        title: "Conflict",
        detail: "This invitation is for a different email address.",
        trace_id: "i1",
      },
    },
  });

  renderAt("/invite#tok3n");

  expect(
    await screen.findByRole("heading", { name: "This invitation can't be used" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "This invitation is for a different email address. (reference i1)",
  );
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && pnpm exec vitest run src/pages/Invite.test.tsx`
Expected: `5 failed`. There's no `/invite` page yet, so the tests find "Page not found" instead (for example `Unable to find role="link" and name "Sign in to accept"`), or stay on `/invite`.

- [ ] **Step 3: Write the page and its route**

`frontend/src/pages/Invite.tsx`:
```tsx
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import { ME_KEY, signInUrl, useMe } from "../auth/session";
import { ErrorNotice } from "../ui/ErrorNotice";
import { Loading } from "../ui/Loading";
import { usePageTitle } from "../ui/usePageTitle";

// The token travels in the link's fragment, which browsers never send to a server (spec §6.3).
// Signing in leaves the app, so it waits in this tab's sessionStorage until it's accepted.
const STORED = "nettriage.invitation";

function keepToken(hash: string): string | null {
  const fromLink = hash.startsWith("#") ? hash.slice(1) : "";
  try {
    if (fromLink !== "") {
      sessionStorage.setItem(STORED, fromLink);
      return fromLink;
    }
    return sessionStorage.getItem(STORED);
  } catch {
    return fromLink === "" ? null : fromLink;
  }
}

function forgetToken(): void {
  try {
    sessionStorage.removeItem(STORED);
  } catch {
    // Storage is blocked, so nothing was kept.
  }
}

function Page({ title, children }: { title: string; children: ReactNode }) {
  usePageTitle(title);
  return (
    <main id="main" className="page narrow">
      <h1>{title}</h1>
      {children}
    </main>
  );
}

/** `/invite#<token>`: signs the person in if needed, then accepts the invitation (spec §10). */
export function Invite() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [token] = useState(() => keepToken(location.hash));
  const me = useMe();
  const accept = useMutation({
    mutationFn: async (value: string) =>
      unwrap(await api.POST("/api/v1/invitations/accept", { body: { token: value } })),
    onSuccess: async (org) => {
      forgetToken();
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
      await navigate(`/app/orgs/${org.id}`, { replace: true });
    },
  });
  const started = useRef(false);

  useEffect(() => {
    // Take the token out of the address bar and the history.
    if (location.hash !== "") {
      void navigate({ pathname: "/invite" }, { replace: true });
    }
  }, [location.hash, navigate]);

  useEffect(() => {
    if (token !== null && me.data && !started.current) {
      started.current = true;
      accept.mutate(token);
    }
  }, [token, me.data, accept]);

  if (token === null) {
    return (
      <Page title="Invitation link incomplete">
        <p>Open the link from your invitation again: it ends with a long code after a #.</p>
      </Page>
    );
  }
  if (me.isPending) {
    return (
      <Page title="You're invited">
        <Loading />
      </Page>
    );
  }
  if (me.isError) {
    return (
      <Page title="You're invited">
        <ErrorNotice error={me.error} />
      </Page>
    );
  }
  if (me.data === null) {
    return (
      <Page title="You're invited">
        <p>
          Sign in, or sign up with the email address the invitation was sent to, and you'll join the
          organization.
        </p>
        <a className="button button-primary" href={signInUrl("/invite")}>
          Sign in to accept
        </a>
      </Page>
    );
  }
  if (accept.isError) {
    return (
      <Page title="This invitation can't be used">
        <ErrorNotice error={accept.error} />
        <p>
          An invitation works once, for 7 days, and only for the email address it was sent to. Ask
          for a new one if you need it.
        </p>
        <Link to="/app">Your organizations</Link>
      </Page>
    );
  }
  return (
    <Page title="You're invited">
      <p role="status">Accepting your invitation…</p>
    </Page>
  );
}
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
import { Navigate, type RouteObject } from "react-router";
import { Landing } from "../pages/Landing";
```
with:
```tsx
import { Navigate, type RouteObject } from "react-router";
import { Invite } from "../pages/Invite";
import { Landing } from "../pages/Landing";
```

In `frontend/src/app/routes.tsx`, replace:
```tsx
  { path: "/", element: <Landing /> },
  {
```
with:
```tsx
  { path: "/", element: <Landing /> },
  { path: "/invite", element: <Invite /> },
  {
```

- [ ] **Step 4: Run the checks**

Run: `just web-check`
Expected:
- lint clean, and `check:api` exits 0;
- `71 passed`, then the four `check-csp` script tests;
- the build succeeds, with `dist/index.html: no inline scripts, styles or event handlers`.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat(web): accept an invitation from its link, through sign-in if needed" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The decisions in the spec, and the owner's steps

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md` (§10, §11.4), `docs/runbooks/setup-and-deploy.md` (B11), `README.md`

- [ ] **Step 1: Amend the spec**

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- `/` Landing: what NetTriage is, "View the live demo", "Sign in / Sign up", and a GitHub link.
- `/demo`: the read-only demo workspace, rendered from the static snapshot, with a clear "demo" banner.
- `/invite`: reads the token from the URL fragment, signs the user in if needed, then accepts.
- `/app`: an org switcher, plus onboarding (create an org or accept an invitation).
- `/app/orgs/:org/uploads`: the uploads list and an upload dialog with progress. The browser computes the file's SHA-256 before requesting a slot.
- `/app/orgs/:org/findings`: a table with filters (severity, status, detector, upload) and sorting.
- `/app/orgs/:org/findings/:id`:
```
with:
```markdown
- `/` Landing: what NetTriage is, "View the live demo", "Sign in / Sign up", and a GitHub link.
- `/demo`: the read-only demo workspace, rendered from the static snapshot, with a clear "demo" banner (Plan 6c, written once Bedrock answers, so the demo's explanations are real; the owner's decision).
- `/invite`: reads the token from the URL fragment, signs the user in if needed, then accepts. The token leaves the address bar at once and waits in the tab's `sessionStorage` while the person signs in (Plan 6a).
- `/app`: an org switcher (the list of the person's organizations, which the product name in the top bar returns to), plus onboarding (create an org or accept an invitation).
- `/app/orgs/:org/uploads`: the uploads list and an upload dialog with progress. The browser computes the file's SHA-256 before requesting a slot.
- `/app/orgs/:org/findings`: a table with filters (severity, status, detector, upload) and sorting by severity or newest, done by the API (the owner's decision; Plan 6b adds `sort` to `GET …/findings`).
- `/app/orgs/:org/findings/:id`:
```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
  - status and assignee controls.
- `/app/orgs/:org/members`: members, roles, invitations.
- `/app/orgs/:org/audit` and `/app/orgs/:org/usage`: Owner and Admin only.
- `/app/settings`: "sign out everywhere".

```
with:
```markdown
  - status and assignee controls.
- `/app/orgs/:org/members`: members, roles, invitations. An invitation's link is shown once, ready to copy.
- `/app/orgs/:org/settings`: rename (Owner, Admin), leave (anyone), and delete after typing the org's name (Owner) (Plan 6a).
- `/app/orgs/:org/audit` and `/app/orgs/:org/usage`: Owner and Admin only.
- `/app/settings`: account settings: who is signed in, and "sign out everywhere".

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- React Router and TanStack Query.
- A typed client generated from the OpenAPI spec, with `openapi-typescript` and `openapi-fetch`. A wrapper adds `x-amz-content-sha256` and `X-CSRF-Token`.

```
with:
```markdown
- React Router and TanStack Query.
- A typed client generated from the OpenAPI document the API exports to `frontend/openapi.json` (`just openapi`), with `openapi-typescript` and `openapi-fetch`. A wrapper adds `x-amz-content-sha256` (sending `{}` on a POST or PUT with no inputs) and `X-CSRF-Token`; a create sends an `Idempotency-Key` (Plan 6a).

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown

**Visual design** is guided by the frontend-design skill during implementation.

```
with:
```markdown

**Visual design** matches the directors' deck (the owner's decision, Plan 6a): navy, teal and off-white, with Space Grotesk headings and IBM Plex Sans text, self-hosted with @fontsource so the CSP needs no other origin. Every style lives in one stylesheet, and a lint rule forbids inline styles and `dangerouslySetInnerHTML`.

```

In `docs/superpowers/specs/2026-09-26-nettriage-m1-design.md`, replace:
```markdown
- **Integration tests** run against real Postgres (testcontainers) with all migrations applied, DynamoDB Local and moto.
- **Contract checks:** an OpenAPI diff on every PR. The generated frontend client stops compiling when the API drifts.
- **End-to-end:** Playwright smoke tests after each deploy:
  - the demo page loads,
```
with:
```markdown
- **Integration tests** run against real Postgres (testcontainers) with all migrations applied, DynamoDB Local and moto.
- **Contract checks:** an OpenAPI diff on every PR. The generated frontend client stops compiling when the API drifts. The API's document is committed as `frontend/openapi.json`, so a PR's diff shows every API change; a backend test fails when it differs from the API, and `pnpm check:api` when the generated client differs from it (Plan 6a).
- **End-to-end:** Playwright smoke tests after each deploy (Plan 7, the owner's decision; they need a test user whose authenticator secret is kept in SSM):
  - the demo page loads,
```

- [ ] **Step 2: Add the owner's walk through the app to the runbook**

In `docs/runbooks/setup-and-deploy.md`, replace:
```markdown

## Part C: when things go wrong
```
with:
```markdown

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
6. Click the **Settings** tab. Rename the organization to `Web Test 2` and click **Save**: the
   new name shows at the top at once.
7. Under **Delete this organization**, type `Web Test 2` and click **Delete organization**. You're
   back on **Your organizations**, without it.
8. Click **Account settings** at the top: your email address shows. Click **Sign out**: you're
   signed out of NetTriage and of Cognito, and the landing page offers **Sign in / Sign up**.

## Part C: when things go wrong
```

- [ ] **Step 3: Update the status and the highlights**

In `README.md`, replace:
```markdown
> deploys, infrastructure as code, telemetry), the detection engine, the Postgres data
> foundation, and sign-in with mandatory MFA.

```
with:
```markdown
> deploys, infrastructure as code, telemetry), the detection engine, the Postgres data
> foundation, sign-in with mandatory MFA, organizations, uploads and their analysis, triage,
> and AI explanations on Bedrock. In progress: the web app.

```

In `README.md`, replace:
```markdown
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```
with:
```markdown
- **AI explanations with guardrails**: each upload's 20 most severe findings are explained by gpt-oss-20b on Amazon Bedrock from the finding's typed fields only. An answer that names an address, port or technique outside the data is refused, and every call is paid for in advance from a daily token budget per org and a $0.50 daily cap across all orgs, which fail closed. Analysts, Admins and Owners can re-run an explanation and rate it, and Owners and Admins see the AI's calls, tokens and cost per day.
- **A typed web app**: React and TypeScript, with an API client generated from the API's OpenAPI document, so CI fails when the two drift. It sends CloudFront's body hash, the CSRF token and idempotency keys on its own, and shows every API error with its reference.
- **Tenant isolation in the database**: row-level security on every tenant table and on users, and a least-privilege database role for each function.
```

- [ ] **Step 4: Check and commit**

Run: `git diff --check`
Expected: no output.

```bash
git add docs README.md
git commit -m "docs: the web app's first pages in the spec, and runbook B11" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9 (Claude, then the owner): Pull request, deploy and a first walk through the app

- [ ] **Step 1 (Claude):**
  - Run `just lint test tools-test edge-test web-check tf-check pin-check cloud-check detection-report build-lambda`; everything must pass.
  - Get a final review of the whole branch, fix what it finds, then push `plan-6a/web-foundation`.
  - Open the PR, watch CI, and request a Copilot review.
- [ ] **Step 2 (owner):** Review the PR, then squash-merge it.
- [ ] **Step 3 (owner):** Runbook B2 (`just deploy-dev`). Expected:
  - `Database migrated.`, with no new logins;
  - `Plan: 0 to add, 3 to change, 0 to destroy`: the `api`, `analyze` and `triage` functions get the new code;
  - the web build is published;
  - fifteen smoke `PASS` lines.
- [ ] **Step 4 (owner):** Runbook B11. Expected: the landing page in the deck's colors; then sign in, create an organization, invite someone and copy the link, rename the organization, delete it, and sign out, all from the app's pages.

## Plan 6a is done when

- [ ] `just lint test web-check` passes locally and CI passes.
- [ ] The PR is merged through review, with every thread resolved.
- [ ] Runbook B11 works on dev.

## Spec coverage of this plan

| Spec | Covered here |
|---|---|
| §2.2 1: sign in and out, sign out everywhere, in the browser | Tasks 3 and 4 |
| §2.2 2: create, rename, delete, invite, accept, change roles, remove, leave | Tasks 4 to 7 |
| §2.2 3 to 7: uploads, findings, triage, the AI panel, the audit log and usage, in the browser | Plan 6b |
| §2.2 8, §4.3: the public demo | Plan 6c |
| §6.2 the CSRF token | Tasks 2 and 3 |
| §6.3 invitations: the link in the fragment, shown once, accepted for the right email | Tasks 5 and 7 |
| §6.4 what each role may do, in the UI | Tasks 5 and 6 |
| §7 SPA request headers, Problem Details, `Idempotency-Key` | Tasks 2 and 4 |
| §7 the findings `sort` parameter | Plan 6b (the owner's decision) |
| §10 `/`, `/invite`, `/app`, `/app/orgs/:org/members`, `/app/settings` | Tasks 3 to 7; Task 8 adds `/app/orgs/:org/settings` to §10 |
| §10 libraries, security, states, accessibility | Tasks 1 to 7; Task 8 amends §10 (the look, the generated client) |
| §10 the uploads, findings, audit and usage pages | Plan 6b |
| §11.4 contract checks | Task 1; Task 8 amends §11.4 |
| §11.4 Playwright smoke tests after each deploy | Plan 7 (the owner's decision); Task 8 amends §11.4 |
