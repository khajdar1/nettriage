import { expect, test } from "vitest";
import { EXPLANATION, aiAnalysis } from "../test/fixtures";
import { failureMessage, pollInterval, readExplanation } from "./explanation";

test("a succeeded answer in output schema v1 is read as an explanation", () => {
  expect(readExplanation(aiAnalysis())).toEqual(EXPLANATION);
});

test("an answer that failed, or in a shape this app doesn't know, is not read", () => {
  expect(readExplanation(aiAnalysis({ status: "failed", output: null }))).toBeNull();
  expect(readExplanation(aiAnalysis({ output_schema_version: "v2" }))).toBeNull();
  expect(readExplanation(aiAnalysis({ output: { ...EXPLANATION, summary: 42 } }))).toBeNull();
  expect(
    readExplanation(aiAnalysis({ output: { ...EXPLANATION, recommended_next_steps: "none" } })),
  ).toBeNull();
});

test("each way an explanation can fail is said plainly", () => {
  expect(failureMessage(aiAnalysis({ status: "skipped_budget", output: null }))).toBe(
    "Not explained: the organization's AI budget for today is used up. It resets at midnight UTC.",
  );
  expect(failureMessage(aiAnalysis({ status: "invalid_output", output: null }))).toBe(
    "The AI's answer didn't pass NetTriage's checks, so it isn't shown.",
  );
  const failed = (code: string) =>
    failureMessage(aiAnalysis({ status: "failed", output: null, error_code: code }));
  expect(failed("ai_disabled")).toBe("AI explanations are switched off right now.");
  expect(failed("provider_throttled")).toBe("The AI service was busy. Try again later.");
  expect(failed("provider_timeout")).toBe("The AI service didn't answer. Try again later.");
});

test("a queued explanation is checked for every few seconds, for five minutes", () => {
  const since = Date.parse("2026-10-04T10:00:00Z");

  expect(pollInterval(since, since + 60_000)).toBe(5000);
  expect(pollInterval(since, since + 5 * 60_000 + 1)).toBe(false);
  expect(pollInterval(null, since)).toBe(false);
});
