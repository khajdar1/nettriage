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
