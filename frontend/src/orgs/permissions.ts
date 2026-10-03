/**
 * What a role may do in an organization (spec §6.4), so the app offers only what will work.
 * The API decides: these only hide controls that would be refused.
 */
import type { Role } from "./roles";

const MANAGED_BY: Record<Role, Role[]> = {
  owner: ["owner", "admin", "analyst", "viewer"],
  admin: ["analyst", "viewer"],
  analyst: [],
  viewer: [],
};

/** The roles someone with `role` may give, and the members they may change or remove. */
export function assignableRoles(role: Role): Role[] {
  return MANAGED_BY[role];
}

export function canInvite(role: Role): boolean {
  return assignableRoles(role).length > 0;
}

/** May `actor` change this member's role or remove them? Never their own: they leave instead. */
export function canManage(actor: Role, member: Role, isSelf: boolean): boolean {
  return !isSelf && assignableRoles(actor).includes(member);
}

export function canRename(role: Role): boolean {
  return role === "owner" || role === "admin";
}

export function canDelete(role: Role): boolean {
  return role === "owner";
}
