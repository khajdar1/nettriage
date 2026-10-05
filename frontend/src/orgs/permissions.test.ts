import { expect, test } from "vitest";
import {
  assignableRoles,
  canContribute,
  canDelete,
  canInvite,
  canManage,
  canReadAudit,
  canReadUsage,
  canRename,
} from "./permissions";
import type { Role } from "./roles";

const ROLES: Role[] = ["owner", "admin", "analyst", "viewer"];

test("an owner gives any role, an admin only analyst or viewer, and no one else any", () => {
  expect(ROLES.map(assignableRoles)).toEqual([
    ["owner", "admin", "analyst", "viewer"],
    ["analyst", "viewer"],
    [],
    [],
  ]);
  expect(ROLES.map(canInvite)).toEqual([true, true, false, false]);
});

test("an admin can't change an owner or another admin", () => {
  expect(canManage("admin", "owner", false)).toBe(false);
  expect(canManage("admin", "admin", false)).toBe(false);
  expect(canManage("admin", "viewer", false)).toBe(true);
});

test("no one changes or removes themselves: they leave instead", () => {
  expect(canManage("owner", "owner", true)).toBe(false);
});

test("owners and admins rename the organization, and only owners delete it", () => {
  expect(ROLES.map(canRename)).toEqual([true, true, false, false]);
  expect(ROLES.map(canDelete)).toEqual([true, false, false, false]);
});

test("owners, admins and analysts do the work: upload, triage, comment and ask the AI", () => {
  expect(ROLES.map(canContribute)).toEqual([true, true, true, false]);
});

test("only owners and admins read the audit log and the AI usage", () => {
  expect(ROLES.map(canReadAudit)).toEqual([true, true, false, false]);
  expect(ROLES.map(canReadUsage)).toEqual([true, true, false, false]);
});
