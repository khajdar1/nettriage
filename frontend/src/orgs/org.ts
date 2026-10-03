/** The organization a page belongs to (`/app/orgs/:orgId/…`), loaded once by its frame. */
import { useQuery } from "@tanstack/react-query";
import { useOutletContext } from "react-router";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Org = components["schemas"]["OrgOut"];

export function orgKey(orgId: string) {
  return ["org", orgId] as const;
}

export function useOrgQuery(orgId: string) {
  return useQuery({
    queryKey: orgKey(orgId),
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/orgs/{org_id}", { params: { path: { org_id: orgId } } })),
  });
}

/** The current organization, for the pages inside its frame. */
export function useOrg(): Org {
  return useOutletContext<Org>();
}
