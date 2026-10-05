import type { components } from "../api/schema";

type AiStatus = components["schemas"]["FindingSummaryOut"]["ai_status"];

/** Whether the AI has explained a finding, beside it in a list (Plan 6d). Teal, as the AI's label. */
export function AiMark({ status }: { status: AiStatus }) {
  if (status === "succeeded") {
    return <span className="ai-mark">Explained</span>;
  }
  return (
    <span className="ai-mark ai-mark-none">
      {status === null || status === "pending" ? "Not explained yet" : "No explanation"}
    </span>
  );
}
