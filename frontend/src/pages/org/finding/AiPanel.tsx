import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../../api/client";
import { unwrap } from "../../../api/problem";
import {
  type AiAnalysis,
  type Explanation,
  failureMessage,
  pollInterval,
  readExplanation,
} from "../../../findings/explanation";
import { type Finding, findingKey, useFinding } from "../../../findings/finding";
import { type Severity, severityLabel } from "../../../findings/vocabulary";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { ErrorNotice } from "../../../ui/ErrorNotice";

/** The model's answer, every part of it plain text (spec §8.4, §10). */
function ExplanationBody({
  explanation,
  detector,
}: {
  explanation: Explanation;
  detector: Severity;
}) {
  const assessment = explanation.severity_assessment;
  return (
    <>
      <p className="ai-summary">{explanation.summary}</p>
      <h3>Why it matters</h3>
      <p>{explanation.why_it_matters}</p>
      {explanation.likely_benign_explanations.length > 0 && (
        <>
          <h3>Harmless explanations to rule out</h3>
          <ul>
            {explanation.likely_benign_explanations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
      <h3>Next steps</h3>
      <ol>
        {explanation.recommended_next_steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className="muted">
        {assessment.agrees_with_detector
          ? `It agrees with the detector's ${severityLabel(detector)}.`
          : `It suggests ${severityLabel(assessment.suggested_severity)} rather than the detector's ${severityLabel(detector)}: ${assessment.reason}`}{" "}
        Confidence: {explanation.confidence}.
      </p>
      {explanation.insufficient_evidence && (
        <p className="notice">The AI says the evidence isn't enough to be sure.</p>
      )}
    </>
  );
}

/** Rating an explanation, for the evals that choose the model (spec §8.5). */
function Feedback({
  org,
  finding,
  analysis,
}: {
  org: Org;
  finding: Finding;
  analysis: AiAnalysis;
}) {
  const queryClient = useQueryClient();
  const rate = useMutation({
    mutationFn: async (feedback: "up" | "down") =>
      unwrap(
        await api.PUT(
          "/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses/{analysis_id}/feedback",
          {
            params: {
              path: { org_id: org.id, finding_id: finding.id, analysis_id: analysis.id },
            },
            body: { feedback },
          },
        ),
      ),
    onSuccess: (rated) =>
      queryClient.setQueryData<Finding>(
        findingKey(org.id, finding.id),
        (old) => old && { ...old, ai_analysis: rated },
      ),
  });
  return (
    <div className="ai-feedback" role="group" aria-label="Rate this explanation">
      <span className="muted">Was it useful?</span>
      <button
        type="button"
        className="button"
        aria-pressed={analysis.feedback === "up"}
        disabled={rate.isPending}
        onClick={() => rate.mutate("up")}
      >
        Useful
      </button>
      <button
        type="button"
        className="button"
        aria-pressed={analysis.feedback === "down"}
        disabled={rate.isPending}
        onClick={() => rate.mutate("down")}
      >
        Not useful
      </button>
      {rate.isError && <ErrorNotice error={rate.error} />}
    </div>
  );
}

/**
 * The AI explanation (spec §10): labeled AI-generated, with its model and prompt version. A
 * contributor rates it, or asks again; a queued request is checked for every few seconds.
 */
export function AiPanel({ org, finding }: { org: Org; finding: Finding }) {
  const queryClient = useQueryClient();
  const key = findingKey(org.id, finding.id);
  const contributor = canContribute(org.role);
  const analysis = finding.ai_analysis;
  const [openedAt] = useState(() => Date.now());
  const [waiting, setWaiting] = useState<{ after: string | null; since: number } | null>(null);
  const [current, setCurrent] = useState(false);
  const answered =
    waiting !== null &&
    analysis !== null &&
    analysis.id !== waiting.after &&
    analysis.status !== "pending";
  const queued = waiting !== null && !answered;
  const since = queued ? waiting.since : analysis?.status === "pending" ? openedAt : null;
  useFinding(org.id, finding.id, { refetchInterval: () => pollInterval(since, Date.now()) });
  const rerun = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/orgs/{org_id}/findings/{finding_id}/ai-analyses", {
          params: { path: { org_id: org.id, finding_id: finding.id } },
        }),
      ),
    onSuccess: async (result) => {
      if (result.status === "explained" && result.ai_analysis !== null) {
        const latest = result.ai_analysis;
        queryClient.setQueryData<Finding>(key, (old) => old && { ...old, ai_analysis: latest });
        setCurrent(true);
        return;
      }
      setCurrent(false);
      setWaiting({ after: analysis?.id ?? null, since: Date.now() });
      await queryClient.invalidateQueries({ queryKey: key });
    },
  });
  const explanation = analysis === null || queued ? null : readExplanation(analysis);
  let body;
  if (queued) {
    body = <p>Queued. The explanation appears here when it's ready.</p>;
  } else if (analysis === null) {
    body = (
      <p>
        Not explained yet.{" "}
        <span className="muted">
          NetTriage explains each upload's 20 most severe findings by itself.
        </span>
      </p>
    );
  } else if (analysis.status === "pending") {
    body = <p>The AI is working on it.</p>;
  } else if (explanation !== null) {
    body = <ExplanationBody explanation={explanation} detector={finding.severity} />;
  } else if (analysis.status === "succeeded") {
    body = (
      <p>
        This explanation is in a format this version of NetTriage can't show. Reload the page to
        update.
      </p>
    );
  } else {
    body = <p>{failureMessage(analysis)}</p>;
  }
  return (
    <section className="ai-panel" aria-labelledby="ai-title">
      <h2 id="ai-title">AI explanation</h2>
      {explanation !== null && analysis !== null && (
        <p className="ai-by">
          <span className="ai-label">AI-generated</span>{" "}
          <span className="muted">
            by <span className="mono">{analysis.model_id}</span>, prompt {analysis.prompt_version}
          </span>
        </p>
      )}
      {body}
      {current && (
        <p role="status" className="muted">
          This explanation is current: nothing changed since it was made, so the AI wasn't asked
          again.
        </p>
      )}
      {contributor && explanation !== null && analysis !== null && (
        <Feedback org={org} finding={finding} analysis={analysis} />
      )}
      {contributor && !queued && analysis?.status !== "pending" && (
        <button
          type="button"
          className="button"
          disabled={rerun.isPending}
          onClick={() => rerun.mutate()}
        >
          {analysis === null ? "Explain this finding" : "Explain again"}
        </button>
      )}
      {rerun.isError && <ErrorNotice error={rerun.error} />}
    </section>
  );
}
