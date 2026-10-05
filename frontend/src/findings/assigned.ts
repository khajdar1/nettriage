/** The unresolved findings assigned to the signed-in person, in each of their organizations. */
import { useQueries } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { Membership } from "../auth/session";
import type { FindingSummary } from "./findings";
import { severityLevel } from "./vocabulary";

/** At most this many per organization; the home links to the rest. */
export const ASSIGNED_LIMIT = 20;

/** Under the org's findings key, so a triage change anywhere in it reads them again. */
export function assignedKey(orgId: string) {
  return ["findings", orgId, "assigned-to-me"] as const;
}

export function useAssignedToMe(memberships: Membership[]) {
  return useQueries({
    queries: memberships.map((membership) => ({
      queryKey: assignedKey(membership.org_id),
      queryFn: async () =>
        unwrap(
          await api.GET("/api/v1/orgs/{org_id}/findings", {
            params: {
              path: { org_id: membership.org_id },
              query: {
                assignee: "me",
                status: ["open", "investigating"],
                sort: "severity",
                limit: ASSIGNED_LIMIT,
              },
            },
          }),
        ),
    })),
  });
}

/** Most severe first, then newest, across organizations. */
export function worstFirst(a: FindingSummary, b: FindingSummary): number {
  const bySeverity = severityLevel(b.severity) - severityLevel(a.severity);
  return bySeverity !== 0 ? bySeverity : Date.parse(b.created_at) - Date.parse(a.created_at);
}
