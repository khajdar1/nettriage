import { expect, test } from "vitest";
import { upload } from "../test/fixtures";
import { isBusy } from "./uploads";

const NOW = Date.parse("2026-10-04T10:00:00Z");

test("an upload being analyzed is busy, and so is one waiting a few minutes for its file", () => {
  expect(isBusy(upload({ status: "processing" }), NOW)).toBe(true);
  expect(
    isBusy(upload({ status: "pending_upload", created_at: "2026-10-04T09:58:00Z" }), NOW),
  ).toBe(true);
});

test("an upload analyzed for over two hours has stopped, and is not watched", () => {
  expect(isBusy(upload({ status: "processing", created_at: "2026-10-04T08:01:00Z" }), NOW)).toBe(
    true,
  );
  expect(isBusy(upload({ status: "processing", created_at: "2026-10-04T07:59:00Z" }), NOW)).toBe(
    false,
  );
});

test("an upload that finished, or whose file never came, is not watched", () => {
  expect(isBusy(upload({ status: "analyzed" }), NOW)).toBe(false);
  expect(isBusy(upload({ status: "failed" }), NOW)).toBe(false);
  expect(
    isBusy(upload({ status: "pending_upload", created_at: "2026-10-04T09:40:00Z" }), NOW),
  ).toBe(false);
});
