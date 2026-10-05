/** An organization's overview (Plan 6d): how its findings stand, read in one call. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Overview = components["schemas"]["OverviewOut"];

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
