/**
 * A finding's AI explanation (spec §8.3): the model's answer in output schema v1, read field by
 * field so a shape this app doesn't know is never shown half right; what each failure means; and
 * how long a queued explanation is waited for.
 */
import type { components } from "../api/schema";
import { type Severity, isSeverity } from "./vocabulary";

export type AiAnalysis = components["schemas"]["AiAnalysisOut"];

export interface Explanation {
  summary: string;
  why_it_matters: string;
  likely_benign_explanations: string[];
  recommended_next_steps: string[];
  attack_techniques: { id: string; rationale: string }[];
  severity_assessment: {
    agrees_with_detector: boolean;
    suggested_severity: Severity;
    reason: string;
  };
  confidence: "low" | "medium" | "high";
  insufficient_evidence: boolean;
}

const POLL_MS = 5000;
const WAIT_MS = 5 * 60_000;

function isText(value: unknown): value is string {
  return typeof value === "string";
}

function isTextList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isText);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The answer, if it succeeded and has exactly the shape of output schema v1; otherwise null. */
export function readExplanation(analysis: AiAnalysis): Explanation | null {
  const output = analysis.output;
  if (
    analysis.status !== "succeeded" ||
    analysis.output_schema_version !== "v1" ||
    output === null
  ) {
    return null;
  }
  const severity = output.severity_assessment;
  const techniques = output.attack_techniques;
  const valid =
    isText(output.summary) &&
    isText(output.why_it_matters) &&
    isTextList(output.likely_benign_explanations) &&
    isTextList(output.recommended_next_steps) &&
    Array.isArray(techniques) &&
    techniques.every((claim) => isRecord(claim) && isText(claim.id) && isText(claim.rationale)) &&
    isRecord(severity) &&
    typeof severity.agrees_with_detector === "boolean" &&
    isText(severity.suggested_severity) &&
    isSeverity(severity.suggested_severity) &&
    isText(severity.reason) &&
    (output.confidence === "low" ||
      output.confidence === "medium" ||
      output.confidence === "high") &&
    typeof output.insufficient_evidence === "boolean";
  return valid ? (output as unknown as Explanation) : null;
}

/** Why there's no explanation to show, in words the person can act on. */
export function failureMessage(analysis: AiAnalysis): string {
  if (analysis.status === "skipped_budget") {
    return "Not explained: the organization's AI budget for today is used up. It resets at midnight UTC.";
  }
  if (analysis.status === "invalid_output" || analysis.error_code === "checks_failed") {
    return "The AI's answer didn't pass NetTriage's checks, so it isn't shown.";
  }
  if (analysis.error_code === "ai_disabled") {
    return "AI explanations are switched off right now.";
  }
  if (analysis.error_code === "provider_throttled") {
    return "The AI service was busy. Try again later.";
  }
  return "The AI service didn't answer. Try again later.";
}

/** How often to check for a queued explanation: every few seconds for five minutes, then not. */
export function pollInterval(waitingSince: number | null, now: number): number | false {
  return waitingSince !== null && now - waitingSince <= WAIT_MS ? POLL_MS : false;
}
