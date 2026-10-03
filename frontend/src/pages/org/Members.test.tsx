import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Role } from "../../orgs/roles";
import { ACME, ADMIN, ORG_ID, OWNER, SIGNED_OUT, VIEWER, memberOf } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";
import { formatDate } from "../../ui/format";

const INVITED = {
  id: "01a10400-0000-7000-8000-000000000001",
  email: "dan@example.com",
  role: "analyst" as const,
  expires_at: "2026-10-10T09:00:00Z",
  created_at: "2026-10-03T09:00:00Z",
  created_by: OWNER.user_id,
};

function row(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(name) });
}

test("an organization opens on its members, under its name and the person's role", async () => {
  signedInAs(OWNER);

  renderAt(`/app/orgs/${ORG_ID}`);

  expect(await screen.findByRole("heading", { level: 1, name: "Members" })).toBeInTheDocument();
  expect(screen.getByText("Acme Security", { selector: ".org-name" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Members" })).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Members · Acme Security · NetTriage");
});

test("an organization that isn't there, or isn't the person's, says so", async () => {
  signedInAs(OWNER, { [`GET ${ORG}`]: { status: 404, body: { title: "Not Found" } } });

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(
    await screen.findByRole("heading", { name: "Organization not found" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Your organizations" })).toHaveAttribute("href", "/app");
});

test("a session that ends during a visit asks to sign in again", async () => {
  let reads = 0;
  signedInAs(OWNER, {
    "GET /api/v1/me": () => (++reads === 1 ? { body: memberOf(ACME) } : SIGNED_OUT),
    [`GET ${ORG}`]: SIGNED_OUT,
  });

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
});

test("members are listed with their roles, and the person is marked", async () => {
  signedInAs(OWNER);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(await screen.findByText("Ben Admin")).toBeInTheDocument();
  expect(within(row("ana@example.com")).getByText("You")).toBeInTheDocument();
  expect(within(row("Ben Admin")).getByText("ben@example.com")).toBeInTheDocument();
  expect(within(row("cleo")).getByText(formatDate(VIEWER.joined_at))).toBeInTheDocument();
});

test("an owner changes a member's role", async () => {
  const fake = signedInAs(OWNER, {
    [`PATCH ${ORG}/members/${VIEWER.user_id}`]: { body: { ...VIEWER, role: "analyst" } },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Role of cleo@example.com" }),
    "Analyst",
  );

  await vi.waitFor(() => expect(fake.requests.some((r) => r.method === "PATCH")).toBe(true));
  const patch = fake.requests.find((request) => request.method === "PATCH");
  expect(await patch?.json()).toEqual({ role: "analyst" });
});

test("an admin changes only analysts and viewers, and never themselves", async () => {
  signedInAs(ADMIN);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  const viewerRole = await screen.findByRole("combobox", { name: "Role of cleo@example.com" });
  const offered = within(viewerRole)
    .getAllByRole("option")
    .map((option) => option.textContent);
  expect(offered).toEqual(["Analyst", "Viewer"]);
  expect(screen.queryByRole("combobox", { name: "Role of ana@example.com" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Role of Ben Admin" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove Ben Admin" })).toBeNull();
});

test("removing a member asks first", async () => {
  const fake = signedInAs(OWNER, { [`DELETE ${ORG}/members/${VIEWER.user_id}`]: { status: 204 } });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.click(await screen.findByRole("button", { name: "Remove cleo@example.com" }));
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(fake.requests.some((request) => request.method === "DELETE")).toBe(false);

  await user.click(screen.getByRole("button", { name: "Remove cleo@example.com" }));
  await user.click(screen.getByRole("button", { name: "Yes, remove" }));

  await vi.waitFor(() => expect(fake.requests.some((r) => r.method === "DELETE")).toBe(true));
});

test("a change the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`PATCH ${ORG}/members/${VIEWER.user_id}`]: {
      status: 403,
      body: { title: "Forbidden", detail: "You can't give a role above your own.", trace_id: "t9" },
    },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Role of cleo@example.com" }),
    "Owner",
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You can't give a role above your own. (reference t9)",
  );
});

test.each<Role>(["analyst", "viewer"])(
  "an %s sees the members and nothing to change",
  async (role) => {
    signedInAs({ ...VIEWER, role });

    renderAt(`/app/orgs/${ORG_ID}/members`);

    expect(await screen.findByText("Ben Admin")).toBeInTheDocument();
    expect(screen.queryAllByRole("combobox")).toEqual([]);
    expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Invitations" })).toBeNull();
  },
);

test("inviting someone shows their link once, ready to copy", async () => {
  let invitations: (typeof INVITED)[] = [];
  const url = "https://d111111abcdef8.cloudfront.net/invite#tok3n";
  const fake = signedInAs(OWNER, {
    [`GET ${ORG}/invitations`]: () => ({ body: { invitations } }),
    [`POST ${ORG}/invitations`]: () => {
      invitations = [INVITED];
      return { status: 201, body: { invitation: INVITED, invite_url: url } };
    },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  await user.type(
    await screen.findByRole("textbox", { name: "Email address" }),
    " dan@example.com ",
  );
  await user.selectOptions(screen.getByRole("combobox", { name: "Role" }), "Analyst");
  await user.click(screen.getByRole("button", { name: "Invite" }));

  expect(await screen.findByRole("textbox", { name: "Invitation link" })).toHaveValue(url);
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.json()).toEqual({ email: "dan@example.com", role: "analyst" });
  expect(await screen.findByRole("cell", { name: "dan@example.com" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Copy link" }));
  expect(await navigator.clipboard.readText()).toBe(url);
  expect(screen.getByText("Copied.")).toBeInTheDocument();
});

test("an admin invites only analysts and viewers", async () => {
  signedInAs(ADMIN);

  renderAt(`/app/orgs/${ORG_ID}/members`);

  const roles = await screen.findByRole("combobox", { name: "Role" });
  expect(
    within(roles)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Analyst", "Viewer"]);
});

test("a pending invitation shows when it expires, and can be revoked", async () => {
  const fake = signedInAs(OWNER, {
    [`GET ${ORG}/invitations`]: { body: { invitations: [INVITED] } },
    [`DELETE ${ORG}/invitations/${INVITED.id}`]: { status: 204 },
  });
  const { user } = renderAt(`/app/orgs/${ORG_ID}/members`);

  expect(
    await screen.findByRole("cell", { name: formatDate(INVITED.expires_at) }),
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Revoke the invitation for dan@example.com" }),
  );

  await vi.waitFor(() => expect(fake.requests.some((r) => r.method === "DELETE")).toBe(true));
});
