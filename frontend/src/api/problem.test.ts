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
