/** An organization's overview (Plan 6d): how its findings stand, read in one call. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";
import { SEVERITIES, STATUSES } from "../findings/vocabulary";

export type Overview = components["schemas"]["OverviewOut"];

/** Open or Investigating, of every severity. */
export function unresolvedCount(overview: Overview): number {
  return SEVERITIES.reduce((sum, severity) => sum + overview.unresolved_by_severity[severity], 0);
}

/** Every finding the organization keeps, whatever its status. */
export function findingCount(overview: Overview): number {
  return STATUSES.reduce((sum, status) => sum + overview.by_status[status], 0);
}

export function overviewKey(orgId: string) {
  return ["overview", orgId] as const;
}

export function useOverview(orgId: string) {
  return useQuery({
    queryKey: overviewKey(orgId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/overview", { params: { path: { org_id: orgId } } }),
      ),
  });
}
