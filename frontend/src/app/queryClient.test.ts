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
