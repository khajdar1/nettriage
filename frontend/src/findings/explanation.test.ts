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

// The status and error code pairs the triage worker stores (explainer.py).
const stored = (status: string, code: string) =>
  failureMessage(aiAnalysis({ status, output: null, error_code: code }));

test("an explanation skipped before any call says why: the switch, or which budget", () => {
  expect(stored("skipped_budget", "ai_disabled")).toBe(
    "AI explanations are switched off right now.",
  );
  expect(stored("skipped_budget", "budget_exhausted_org")).toBe(
    "Not explained: the organization's AI budget for today is used up. It resets at midnight UTC.",
  );
  expect(stored("skipped_budget", "budget_exhausted_global")).toBe(
    "Not explained: NetTriage's AI budget for today, shared by every organization, is used up. It resets at midnight UTC.",
  );
  expect(stored("skipped_budget", "budget_unavailable")).toBe(
    "Not explained: NetTriage couldn't check the AI budget, so it didn't ask the AI. Try again later.",
  );
});

test("an explanation that failed or was refused says which", () => {
  expect(stored("invalid_output", "checks_failed")).toBe(
    "The AI's answer didn't pass NetTriage's checks, so it isn't shown.",
  );
  expect(stored("failed", "provider_throttled")).toBe("The AI service was busy. Try again later.");
  expect(stored("failed", "provider_timeout")).toBe(
    "The AI service didn't answer. Try again later.",
  );
});

test("a queued explanation is checked for every few seconds, for five minutes", () => {
  const since = Date.parse("2026-10-04T10:00:00Z");

  expect(pollInterval(since, since + 60_000)).toBe(5000);
  expect(pollInterval(since, since + 5 * 60_000 + 1)).toBe(false);
  expect(pollInterval(null, since)).toBe(false);
});
