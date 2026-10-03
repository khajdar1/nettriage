import { screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { ACME, ME, ORG_ID, SIGNED_OUT, memberOf } from "../test/fixtures";
import { renderAt } from "../test/render";

const CREATED = {
  id: ORG_ID,
  name: "Acme Security",
  slug: "acme-security",
  role: "owner",
  member_count: 1,
  created_at: "2026-10-03T19:17:58Z",
};

test("signed out, the app asks to sign in and comes back to the same page", async () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/app/settings");

  expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
    "href",
    "/api/auth/login?return_to=%2Fapp%2Fsettings",
  );
});

test("someone new is shown how to start", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/app");

  expect(await screen.findByRole("heading", { name: "Your organizations" })).toBeInTheDocument();
  expect(
    screen.getByText(
      "Create your first organization, or open an invitation link someone sent you.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Organization name" })).toBeInTheDocument();
});

test("each organization is listed with the person's role in it", async () => {
  const lab = {
    ...ACME,
    org_id: "01a10333-0000-7000-8000-000000000002",
    name: "Lab",
    role: "viewer",
  };
  fakeApi({ "GET /api/v1/me": { body: memberOf(ACME, lab) } });

  renderAt("/app");

  expect(await screen.findByRole("link", { name: "Acme Security" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}`,
  );
  expect(screen.getByRole("link", { name: "Lab" })).toBeInTheDocument();
  expect(screen.getByText("Owner")).toBeInTheDocument();
  expect(screen.getByText("Viewer")).toBeInTheDocument();
});

test("creating an organization sends its name once, then opens it", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/v1/orgs": { status: 201, body: CREATED },
  });
  const { user, router } = renderAt("/app");

  await user.type(
    await screen.findByRole("textbox", { name: "Organization name" }),
    "  Acme Security ",
  );
  await user.click(screen.getByRole("button", { name: "Create organization" }));

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  expect(fake.requests.filter((request) => request.method === "POST")).toHaveLength(1);
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.json()).toEqual({ name: "Acme Security" });
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
});

test("a create the API refuses says why", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/v1/orgs": {
      status: 409,
      body: {
        title: "Conflict",
        detail: "You can belong to at most 3 organizations.",
        trace_id: "abc123",
      },
    },
  });
  const { user } = renderAt("/app");

  await user.type(await screen.findByRole("textbox", { name: "Organization name" }), "Fourth");
  await user.click(screen.getByRole("button", { name: "Create organization" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "You can belong to at most 3 organizations. (reference abc123)",
  );
});

test("at three organizations there's no way to create a fourth", async () => {
  const orgs = ["1", "2", "3"].map((n) => ({
    ...ACME,
    org_id: `01a10333-0000-7000-8000-00000000000${n}`,
    name: `Org ${n}`,
  }));
  fakeApi({ "GET /api/v1/me": { body: memberOf(...orgs) } });

  renderAt("/app");

  expect(
    await screen.findByText("You belong to 3 organizations, the most there can be."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Organization name" })).not.toBeInTheDocument();
});

test("a create sent again after a failure reuses its key, so it can't make two", async () => {
  let posts = 0;
  const fake = fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/v1/orgs": () =>
      ++posts === 1
        ? { status: 503, body: { title: "Service Unavailable", trace_id: "u1" } }
        : { status: 201, body: CREATED },
  });
  const { user, router } = renderAt("/app");

  await user.type(await screen.findByRole("textbox", { name: "Organization name" }), "Acme");
  await user.click(screen.getByRole("button", { name: "Create organization" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "Create organization" }));

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  const keys = fake.requests
    .filter((request) => request.method === "POST")
    .map((request) => request.headers.get("Idempotency-Key"));
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
});
