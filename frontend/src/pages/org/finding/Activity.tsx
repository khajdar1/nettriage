import type { components } from "../../../api/schema";
import type { Finding } from "../../../findings/finding";
import { isStatus, statusLabel } from "../../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../../orgs/members";
import type { Org } from "../../../orgs/org";
import { canContribute } from "../../../orgs/permissions";
import { Ago } from "../../../ui/Ago";
import { CommentForm } from "./CommentForm";

type FindingEvent = components["schemas"]["FindingEventOut"];

function status(value: unknown): string {
  return typeof value === "string" && isStatus(value) ? statusLabel(value) : "another status";
}

/** One change in the finding's history, as a sentence. */
function sentence(event: FindingEvent, members: Member[] | undefined): string {
  const who = event.actor_id === null ? "NetTriage" : memberName(members, event.actor_id);
  const { from, to } = event.payload;
  switch (event.type) {
    case "created":
      return `${who} found it`;
    case "status_changed":
      return `${who} changed the status from ${status(from)} to ${status(to)}`;
    case "assigned":
      if (typeof to !== "string") {
        return `${who} unassigned it`;
      }
      return to === event.actor_id
        ? `${who} took it on`
        : `${who} assigned it to ${memberName(members, to)}`;
    case "commented":
      return `${who} commented`;
    case "ai_explained":
      return "The AI explained it";
    default:
      return `${who} changed it`;
  }
}

/** The finding's history, oldest first, and adding a comment to it (spec §10). */
export function Activity({ org, finding }: { org: Org; finding: Finding }) {
  const members = useMembers(org.id);
  return (
    <section className="activity" aria-labelledby="activity-title">
      <h2 id="activity-title">Activity</h2>
      {finding.events_total > finding.events.length && (
        <p className="muted">
          Showing the latest {finding.events.length} of {finding.events_total} events.
        </p>
      )}
      <ol className="timeline">
        {finding.events.map((event) => (
          <li key={event.id}>
            <span className="event-what">{sentence(event, members.data)}</span>{" "}
            <span className="muted">
              <Ago iso={event.created_at} />
            </span>
            {event.type === "commented" && typeof event.payload.text === "string" && (
              <p className="comment">{event.payload.text}</p>
            )}
          </li>
        ))}
      </ol>
      {canContribute(org.role) && <CommentForm org={org} findingId={finding.id} />}
    </section>
  );
}
