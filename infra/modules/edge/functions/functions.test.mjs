import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function load(name) {
  const code = readFileSync(new URL(`./${name}`, import.meta.url), "utf8");
  return vm.runInNewContext(`${code}\n;handler`, {});
}

const apiEdgeCheck = load("api-edge-check.js");
const spaRewrite = load("spa-rewrite.js");

function event(uri, cookies = {}) {
  return { request: { method: "GET", uri, headers: {}, cookies, querystring: {} } };
}

test("health check passes without a cookie", () => {
  assert.equal(apiEdgeCheck(event("/api/health")).uri, "/api/health");
});

test("sign-in routes pass without a cookie", () => {
  assert.equal(apiEdgeCheck(event("/api/auth/login")).uri, "/api/auth/login");
});

test("other API calls without a session cookie get 401 problem+json", () => {
  const response = apiEdgeCheck(event("/api/v1/me"));
  assert.equal(response.statusCode, 401);
  assert.equal(response.headers["content-type"].value, "application/problem+json");
  assert.equal(response.headers["cache-control"].value, "no-store");
});

test("look-alike paths are not public", () => {
  assert.equal(apiEdgeCheck(event("/api/healthz")).statusCode, 401);
  assert.equal(apiEdgeCheck(event("/api/authx")).statusCode, 401);
});

test("an empty session cookie is rejected", () => {
  const response = apiEdgeCheck(event("/api/v1/me", { "__Host-session": { value: "" } }));
  assert.equal(response.statusCode, 401);
});

test("a request with a session cookie passes through", () => {
  const request = apiEdgeCheck(event("/api/v1/me", { "__Host-session": { value: "abc" } }));
  assert.equal(request.uri, "/api/v1/me");
});

test("client-side routes are served index.html", () => {
  assert.equal(spaRewrite(event("/app/findings")).uri, "/index.html");
  assert.equal(spaRewrite(event("/")).uri, "/index.html");
});

test("files keep their path", () => {
  assert.equal(spaRewrite(event("/assets/index-abc.js")).uri, "/assets/index-abc.js");
  assert.equal(spaRewrite(event("/demo/findings.json")).uri, "/demo/findings.json");
});

test("API paths are never rewritten to index.html", () => {
  assert.equal(spaRewrite(event("/api/does-not-exist")).uri, "/api/does-not-exist");
});

test("401 response includes Problem Details body", () => {
  const response = apiEdgeCheck(event("/api/v1/me"));
  assert.equal(response.body.encoding, "text");
  const body = JSON.parse(response.body.data);
  assert.equal(body.status, 401);
  assert.equal(body.title, "Unauthorized");
  assert.equal(body.type, "about:blank");
  assert.equal(body.instance, "/api/v1/me");
  assert("trace_id" in body);
});

test("401 response escapes special characters in instance URI", () => {
  const response = apiEdgeCheck(event('/api/v1/"x'));
  const body = JSON.parse(response.body.data);
  assert.equal(body.instance, '/api/v1/"x');
});
