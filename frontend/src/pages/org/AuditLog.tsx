import { Link } from "react-router";
import {
  type AuditEvent,
  actionLabel,
  detailsText,
  outcomeLabel,
  useAuditLog,
} from "../../audit/auditLog";
import { type Member, memberName, useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { canReadAudit } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";

function who(event: AuditEvent, members: Member[] | undefined): string {
  if (event.actor_type === "system") {
    return "NetTriage";
  }
  return event.actor_user_id === null
    ? "Someone not signed in"
    : memberName(members, event.actor_user_id);
}

function EventRow({ event, members }: { event: AuditEvent; members?: Member[] }) {
  const label = actionLabel(event.action);
  const details = detailsText(event.details);
  return (
    <tr>
      <td className="date">{formatDateTime(event.created_at)}</td>
      <td>{who(event, members)}</td>
      <td>
        {label !== null && <div>{label}</div>}
        <div className="mono muted">{event.action}</div>
        {details !== "" && <div className="mono muted">{details}</div>}
        {event.target_type === "finding" && event.target_id !== null && (
          <Link to={`../findings/${event.target_id}`}>The finding</Link>
        )}
      </td>
      <td>
        <span className="state">{outcomeLabel(event.outcome)}</span>
      </td>
    </tr>
  );
}

/** `/app/orgs/:org/audit`: every recorded action in the organization, for Owners and Admins. */
export function AuditLog() {
  const org = useOrg();
  usePageTitle(`Audit log · ${org.name}`);
  const allowed = canReadAudit(org.role);
  const log = useAuditLog(org.id, allowed);
  const members = useMembers(org.id);
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];
  return (
    <main id="main" className="page">
      <div className="page-head">
        <h1>Audit log</h1>
        <span className="muted">Events are kept for 180 days.</span>
      </div>
      {!allowed && <p>Only Owners and Admins can see the audit log.</p>}
      {log.isLoading && <Loading />}
      {log.isError && <ErrorNotice error={log.error} />}
      {log.isSuccess && events.length === 0 && <p className="muted">Nothing recorded yet.</p>}
      {events.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Audit log of {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Who</th>
              <th scope="col">What</th>
              <th scope="col">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <EventRow key={event.id} event={event} members={members.data} />
            ))}
          </tbody>
        </table>
      )}
      {log.hasNextPage && (
        <button
          type="button"
          className="button more"
          disabled={log.isFetchingNextPage}
          onClick={() => void log.fetchNextPage()}
        >
          Show older events
        </button>
      )}
    </main>
  );
}
