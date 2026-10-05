import { Link } from "react-router";
import { useMe } from "../../../auth/session";
import { type AiAnalysis, readExplanation } from "../../../findings/explanation";
import type { Finding } from "../../../findings/finding";
import { searchFrom } from "../../../findings/findings";
import { type Severity, severityLabel, statusLabel } from "../../../findings/vocabulary";
import { memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { Ago } from "../../../ui/Ago";
import { formatWindow } from "../../../ui/format";
import { Person, Unassigned } from "../../../ui/Person";
import { useUpload } from "../../../uploads/uploads";

/** The AI's verdict in a few words; the AI panel below says the rest, and why a failure failed. */
export function aiVerdict(analysis: AiAnalysis | null, severity: Severity): string {
  if (analysis === null) {
    return "Not explained yet";
  }
  if (analysis.status === "pending") {
    return "Queued";
  }
  const explanation = readExplanation(analysis);
  if (explanation === null) {
    return "No explanation";
  }
  const assessment = explanation.severity_assessment;
  return assessment.agrees_with_detector
    ? `Agrees: ${severityLabel(severity)}, ${explanation.confidence} confidence`
    : `Suggests ${severityLabel(assessment.suggested_severity)}`;
}

/** The upload's file name, linking to all its findings; shown once the upload has been read. */
function UploadTerm({ org, finding }: { org: Org; finding: Finding }) {
  const upload = useUpload(org.id, finding.upload_id);
  if (upload.data === undefined) {
    return null;
  }
  const search = searchFrom({ status: "any", upload: finding.upload_id, sort: "severity" });
  return (
    <div>
      <dt>Upload</dt>
      <dd>
        <Link
          className="mono strip-file"
          to={`../findings?${search.toString()}`}
          title={upload.data.original_filename}
        >
          {upload.data.original_filename}
        </Link>
      </dd>
    </div>
  );
}

/** Under a finding's title: where it stands, in one line (Plan 6d). */
export function SummaryStrip({ org, finding }: { org: Org; finding: Finding }) {
  const members = useMembers(org.id);
  const me = useMe();
  return (
    <dl className="strip">
      <div>
        <dt>Status</dt>
        <dd>
          <span className="state">{statusLabel(finding.status)}</span>
        </dd>
      </div>
      <div>
        <dt>Assignee</dt>
        <dd>
          {finding.assignee_id === null ? (
            <Unassigned />
          ) : (
            <Person
              name={memberName(members.data, finding.assignee_id)}
              you={finding.assignee_id === me.data?.user.id}
            />
          )}
        </dd>
      </div>
      <div>
        <dt>Detected</dt>
        <dd>
          <Ago iso={finding.created_at} />
        </dd>
      </div>
      <div>
        <dt>Window</dt>
        <dd>{formatWindow(finding.window_start, finding.window_end)}</dd>
      </div>
      <UploadTerm org={org} finding={finding} />
      <div>
        <dt>AI</dt>
        <dd className={finding.ai_analysis?.status === "succeeded" ? "verdict" : undefined}>
          {aiVerdict(finding.ai_analysis, finding.severity)}
        </dd>
      </div>
    </dl>
  );
}
