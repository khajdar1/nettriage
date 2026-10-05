/** One finding in full (spec §7): evidence, techniques, history and its latest AI analysis. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Finding = components["schemas"]["FindingOut"];
export type Evidence = components["schemas"]["EvidenceOut"];
export type Technique = components["schemas"]["FindingTechniqueOut"];

export function findingKey(orgId: string, findingId: string) {
  return ["finding", orgId, findingId] as const;
}

export function useFinding(orgId: string, findingId: string) {
  return useQuery({
    queryKey: findingKey(orgId, findingId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/findings/{finding_id}", {
          params: { path: { org_id: orgId, finding_id: findingId } },
        }),
      ),
  });
}
