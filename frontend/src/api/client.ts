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
