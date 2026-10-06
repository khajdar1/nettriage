import { Link } from "react-router";
import type { Membership } from "../../auth/session";
import { SEVERITIES, severityLabel, severityLevel } from "../../findings/vocabulary";
import { type Overview, useOverview } from "../../orgs/overview";
import { roleLabel } from "../../orgs/roles";
import { Ago } from "../../ui/Ago";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatNumber } from "../../ui/format";
import { SeverityBars } from "../../ui/SeverityBars";
import { UPLOAD_STATUS_LABELS } from "../../uploads/uploads";

/** Unresolved findings by severity, each count a link to them; a zero is greyed, not hidden. */
function SeverityCounts({ base, overview }: { base: string; overview: Overview }) {
  return (
    <div className="mini-counts">
      {SEVERITIES.map((severity) => {
        const count = overview.unresolved_by_severity[severity];
        return (
          <Link
            key={severity}
            to={`${base}/findings?severity=${severity}`}
            className={count === 0 ? "zero" : undefined}
            aria-label={`${count} ${severityLabel(severity)} findings, unresolved`}
          >
            <SeverityBars level={severityLevel(severity)} />
            <b>{formatNumber(count)}</b> {severityLabel(severity)}
          </Link>
        );
      })}
    </div>
  );
}

function Facts({ base, overview }: { base: string; overview: Overview }) {
  const upload = overview.last_upload;
  return (
    <dl className="org-facts">
      <div>
        <dt>Unassigned</dt>
        <dd>
          <Link
            to={`${base}/findings?assignee=none`}
            aria-label={`${overview.unresolved_unassigned} unresolved findings, unassigned`}
          >
            {formatNumber(overview.unresolved_unassigned)}
          </Link>
        </dd>
      </div>
      <div>
        <dt>Yours</dt>
        <dd>
          <Link
            to={`${base}/findings?assignee=me`}
            aria-label={`${overview.unresolved_mine} unresolved findings, assigned to you`}
          >
            {formatNumber(overview.unresolved_mine)}
          </Link>
        </dd>
      </div>
      <div>
        <dt>Members</dt>
        <dd>{formatNumber(overview.member_count)}</dd>
      </div>
      <div>
        <dt>Last upload</dt>
        <dd>
          {upload === null ? (
            "None yet"
          ) : (
            <>
              <Ago iso={upload.created_at} />, {UPLOAD_STATUS_LABELS[upload.status].toLowerCase()}
            </>
          )}
        </dd>
      </div>
    </dl>
  );
}

/** One organization on the home page: how its findings stand, and the way into them (Plan 6d). */
export function OrgCard({ membership }: { membership: Membership }) {
  const overview = useOverview(membership.org_id);
  const base = `/app/orgs/${membership.org_id}`;
  const nameId = `org-${membership.org_id}`;
  const settled = overview.data
    ? overview.data.by_status.resolved + overview.data.by_status.false_positive
    : 0;
  return (
    <article className="org-card" aria-labelledby={nameId}>
      <div className="org-card-head">
        <h3 id={nameId}>
          <Link to={base}>{membership.name}</Link>
        </h3>
        <span className="muted">{roleLabel(membership.role)}</span>
      </div>
      {overview.isError && <ErrorNotice error={overview.error} />}
      {overview.data && (
        <>
          <SeverityCounts base={base} overview={overview.data} />
          <Facts base={base} overview={overview.data} />
        </>
      )}
      <div className="org-card-foot">
        <Link className="button button-primary" to={`${base}/findings`}>
          Open findings
        </Link>
        {overview.data && (
          <span className="muted">{formatNumber(settled)} resolved or false positive</span>
        )}
      </div>
    </article>
  );
}
