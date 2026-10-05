/** An organization's audit log (spec §9.4): who did what, newest first, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type AuditEvent = components["schemas"]["AuditEventOut"];

/** The actions the API records, in words; an action added later is shown by its code. */
const ACTIONS: Record<string, string> = {
  "org.created": "Created the organization",
  "org.renamed": "Renamed the organization",
  "org.deleted": "Deleted the organization",
  "member.invited": "Invited someone",
  "member.joined": "Joined",
  "member.left": "Left",
  "member.removed": "Removed a member",
  "member.role_changed": "Changed a member's role",
  "invitation.revoked": "Revoked an invitation",
  "upload.created": "Uploaded a flow log",
  "finding.status_changed": "Changed a finding's status",
  "finding.assigned": "Assigned a finding",
  "finding.commented": "Commented on a finding",
  "ai.rerun_requested": "Asked the AI to explain a finding again",
  "budget.exhausted": "Used up an AI budget",
  "authz.denied": "Was refused an action",
  "ratelimit.limited": "Was slowed down for sending too many requests",
  "auth.session_created": "Signed in",
  "auth.logout": "Signed out",
  "auth.logout_all": "Signed out everywhere",
};

const OUTCOMES: Record<string, string> = { success: "Done", denied: "Refused", error: "Failed" };

export function actionLabel(action: string): string | null {
  return ACTIONS[action] ?? null;
}

export function outcomeLabel(outcome: string): string {
  return OUTCOMES[outcome] ?? outcome;
}

/** An event's details on one line, such as "from: open, to: resolved". */
export function detailsText(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => {
      const shown =
        typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
      return `${key}: ${shown}`;
    })
    .join(", ");
}

export function useAuditLog(orgId: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ["audit-log", orgId],
    enabled,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/audit-log", {
          params: { path: { org_id: orgId }, query: pageParam ? { cursor: pageParam } : {} },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
