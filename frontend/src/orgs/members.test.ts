import { expect, test } from "vitest";
import { ADMIN, OWNER } from "../test/fixtures";
import { memberName } from "./members";

test("a member is named by their display name, else their email", () => {
  expect(memberName([OWNER, ADMIN], ADMIN.user_id)).toBe("Ben Admin");
  expect(memberName([OWNER, ADMIN], OWNER.user_id)).toBe("ana@example.com");
});

test("someone no longer in the organization is named plainly, and so is anyone still loading", () => {
  expect(memberName([OWNER], "01a0e9e9-0000-7000-8000-0000000000ff")).toBe("A former member");
  expect(memberName(undefined, OWNER.user_id)).toBe("A member");
});
