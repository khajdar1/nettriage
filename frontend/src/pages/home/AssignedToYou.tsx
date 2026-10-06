import { Link } from "react-router";
import type { Membership } from "../../auth/session";
import { useAssignedToMe, worstFirst } from "../../findings/assigned";
import { statusLabel } from "../../findings/vocabulary";
import { useOverview } from "../../orgs/overview";
import { Ago } from "../../ui/Ago";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { FindingSubline } from "../../ui/FindingSubline";
import { Loading } from "../../ui/Loading";
import { Severity } from "../../ui/Severity";

/** "N more in Acme": what the overview counts as yours, beyond what was fetched. */
function MoreIn({ membership, shown }: { membership: Membership; shown: number }) {
  const overview = useOverview(membership.org_id);
  const more = (overview.data?.unresolved_mine ?? 0) - shown;
  return (
    <Link to={`/app/orgs/${membership.org_id}/findings?assignee=me`}>
      {more > 0 ? `${more} more` : "More"} in {membership.name}
    </Link>
  );
}

/** The unresolved findings assigned to the person, in every organization, worst first (Plan 6d). */
export function AssignedToYou({ memberships }: { memberships: Membership[] }) {
  const results = useAssignedToMe(memberships);
  const rows = results.flatMap((result, index) =>
    (result.data?.findings ?? []).map((finding) => ({
      finding,
      membership: memberships[index] as Membership,
    })),
  );
  rows.sort((a, b) => worstFirst(a.finding, b.finding));
  const orgs = new Set(rows.map((row) => row.membership.org_id)).size;
  const loading = results.some((result) => result.isPending);
  const capped = results.flatMap((result, index) =>
    result.data?.next_cursor
      ? [{ membership: memberships[index] as Membership, shown: result.data.findings.length }]
      : [],
  );
  return (
    <section className="home-section" aria-labelledby="assigned-title">
      <div className="page-head">
        <h2 id="assigned-title" className="page-title">
          Assigned to you
        </h2>
        {!loading && rows.length > 0 && (
          <span className="muted">
            {rows.length} unresolved, in {orgs} {orgs === 1 ? "organization" : "organizations"}
          </span>
        )}
      </div>
      {results.map((result, index) =>
        result.isError ? (
          <ErrorNotice key={memberships[index]?.org_id} error={result.error} />
        ) : null,
      )}
      {loading && <Loading />}
      {!loading && rows.length === 0 && (
        <p className="muted">
          Nothing is assigned to you. Pick up an unassigned finding in one of your organizations.
        </p>
      )}
      {rows.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Findings assigned to you</caption>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Finding</th>
              <th scope="col">Status</th>
              <th scope="col">Organization</th>
              <th scope="col">Detected</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ finding, membership }) => (
              <tr key={finding.id}>
                <td>
                  <Severity severity={finding.severity} />
                </td>
                <td>
                  <Link to={`/app/orgs/${membership.org_id}/findings/${finding.id}`}>
                    {finding.title}
                  </Link>
                  <FindingSubline finding={finding} />
                </td>
                <td>
                  <span className="state">{statusLabel(finding.status)}</span>
                </td>
                <td>{membership.name}</td>
                <td>
                  <Ago iso={finding.created_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {capped.map(({ membership, shown }) => (
        <p key={membership.org_id} className="more">
          <MoreIn membership={membership} shown={shown} />
        </p>
      ))}
    </section>
  );
}
