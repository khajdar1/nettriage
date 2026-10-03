import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Member } from "./MemberRow";
import { ACME, ADMIN, ORG_ID, OWNER, SIGNED_OUT, VIEWER, memberOf, org } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const SETTINGS = `/app/orgs/${ORG_ID}/settings`;

function sections(): string[] {
  return screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent ?? "");
}

test.each<[Member, string[]]>([
  [OWNER, ["Name", "Leave this organization", "Delete this organization"]],
  [ADMIN, ["Name", "Leave this organization"]],
  [VIEWER, ["Leave this organization"]],
])("a member sees only the settings their role allows", async (self, expected) => {
  signedInAs(self);

  renderAt(SETTINGS);

  expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  expect(sections()).toEqual(expected);
  expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
});

test("renaming saves the new name and shows it at once", async () => {
  const fake = signedInAs(OWNER, {
    [`PATCH ${ORG}`]: { body: { ...org(), name: "Acme" } },
  });
  const { user } = renderAt(SETTINGS);

  const name = await screen.findByRole("textbox", { name: "Organization name" });
  await user.clear(name);
  await user.type(name, " Acme ");
  await user.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("Saved.")).toBeInTheDocument();
  expect(screen.getByText("Acme", { selector: ".org-name" })).toBeInTheDocument();
  const patch = fake.requests.find((request) => request.method === "PATCH");
  expect(await patch?.json()).toEqual({ name: "Acme" });
});

test("a rename the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`PATCH ${ORG}`]: {
      status: 422,
      body: { title: "Unprocessable Content", detail: "The name can't be blank.", trace_id: "r1" },
    },
  });
  const { user } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Save" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The name can't be blank. (reference r1)",
  );
});

test("deleting needs the organization's exact name, then leaves for the organization list", async () => {
  const fake = signedInAs(OWNER, { [`DELETE ${ORG}`]: { status: 204 } });
  const { user, router } = renderAt(SETTINGS);

  const confirm = await screen.findByRole("textbox", { name: "Type Acme Security to confirm" });
  const button = screen.getByRole("button", { name: "Delete organization" });
  await user.type(confirm, "acme security");
  expect(button).toBeDisabled();
  await user.clear(confirm);
  await user.type(confirm, "Acme Security");
  await user.click(button);

  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  const deleted = fake.requests.find((request) => request.method === "DELETE");
  expect(deleted?.url).toBe(`http://localhost:3000${ORG}?confirm_name=Acme%20Security`);
});

test("leaving asks first, then leaves for the organization list", async () => {
  const fake = signedInAs(VIEWER, { [`DELETE ${ORG}/members/${VIEWER.user_id}`]: { status: 204 } });
  const { user, router } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Leave" }));
  expect(screen.getByRole("button", { name: "Yes, leave Acme Security" })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Leave" })).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Leave" }));
  await user.click(screen.getByRole("button", { name: "Yes, leave Acme Security" }));

  await vi.waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  expect(fake.requests.some((request) => request.method === "DELETE")).toBe(true);
});

test("the last owner can't leave, and is told why", async () => {
  signedInAs(OWNER, {
    [`DELETE ${ORG}/members/${OWNER.user_id}`]: {
      status: 409,
      body: {
        title: "Conflict",
        detail: "An organization keeps at least one owner.",
        trace_id: "l1",
      },
    },
  });
  const { user, router } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Leave" }));
  await user.click(screen.getByRole("button", { name: "Yes, leave Acme Security" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "An organization keeps at least one owner. (reference l1)",
  );
  expect(router.state.location.pathname).toBe(SETTINGS);
});

test("a change after the session ended asks to sign in again, then comes back here", async () => {
  let reads = 0;
  signedInAs(OWNER, {
    "GET /api/v1/me": () => (++reads === 1 ? { body: memberOf(ACME) } : SIGNED_OUT),
    [`PATCH ${ORG}`]: SIGNED_OUT,
  });
  const { user } = renderAt(SETTINGS);

  await user.click(await screen.findByRole("button", { name: "Save" }));

  expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
    "href",
    `/api/auth/login?return_to=${encodeURIComponent(SETTINGS)}`,
  );
});
