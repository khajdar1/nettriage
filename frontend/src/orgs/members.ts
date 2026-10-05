/** An organization's members (spec §7), read once and shared by every page that names people. */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Member = components["schemas"]["MemberOut"];

export function membersKey(orgId: string) {
  return ["members", orgId] as const;
}

export function useMembers(orgId: string) {
  return useQuery({
    queryKey: membersKey(orgId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/members", { params: { path: { org_id: orgId } } }),
      ).members,
  });
}

/** Who someone is, as their teammates know them: display name, else email. */
export function memberName(members: Member[] | undefined, userId: string): string {
  if (members === undefined) {
    return "A member";
  }
  const member = members.find((candidate) => candidate.user_id === userId);
  return member === undefined ? "A former member" : (member.display_name ?? member.email);
}
