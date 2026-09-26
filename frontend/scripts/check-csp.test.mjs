import assert from "node:assert/strict";
import { test } from "node:test";
import { findCspViolations } from "./check-csp.mjs";

test("external module scripts pass", () => {
  const html = '<script type="module" crossorigin src="/assets/index-abc.js"></script>';
  assert.deepEqual(findCspViolations(html), []);
});

test("inline scripts fail", () => {
  assert.deepEqual(findCspViolations("<script>alert(1)</script>"), ["inline <script>"]);
});

test("inline styles and event handlers fail", () => {
  const html = '<style>a{}</style><div style="color:red" onclick="go()"></div>';
  assert.deepEqual(findCspViolations(html), [
    "inline <style>",
    "style attribute",
    "inline event handler",
  ]);
});
