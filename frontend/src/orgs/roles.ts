import type { components } from "../api/schema";

export type Role = components["schemas"]["MemberOut"]["role"];

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  analyst: "Analyst",
  viewer: "Viewer",
};

export function roleLabel(role: string): string {
  return role in ROLE_LABELS ? ROLE_LABELS[role as Role] : role;
}
