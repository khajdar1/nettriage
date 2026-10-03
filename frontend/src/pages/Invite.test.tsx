import { screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { ME, ORG_ID, SIGNED_OUT, org } from "../test/fixtures";
import { renderAt } from "../test/render";

const STORED = "nettriage.invitation";
const ACCEPT = "POST /api/v1/invitations/accept";

afterEach(() => sessionStorage.clear());

test("a link without its code says so", () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/invite");

  expect(screen.getByRole("heading", { name: "Invitation link incomplete" })).toBeInTheDocument();
});

test("signed out, it keeps the code, clears the address bar and asks to sign in", async () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  const { router } = renderAt("/invite#tok3n");

  expect(await screen.findByRole("link", { name: "Sign in to accept" })).toHaveAttribute(
    "href",
    "/api/auth/login?return_to=%2Finvite",
  );
  expect(sessionStorage.getItem(STORED)).toBe("tok3n");
  await vi.waitFor(() => expect(router.state.location.hash).toBe(""));
});

test("back from signing in, it accepts with the kept code and opens the organization", async () => {
  sessionStorage.setItem(STORED, "tok3n");
  const fake = fakeApi({ "GET /api/v1/me": { body: ME }, [ACCEPT]: { body: org("analyst") } });

  const { router } = renderAt("/invite");

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  const accepted = fake.requests.filter((request) => request.method === "POST");
  expect(accepted).toHaveLength(1);
  expect(await accepted[0]?.json()).toEqual({ token: "tok3n" });
  expect(sessionStorage.getItem(STORED)).toBeNull();
});

test("a link opened while signed in is accepted once, right away", async () => {
  const fake = fakeApi({ "GET /api/v1/me": { body: ME }, [ACCEPT]: { body: org("viewer") } });

  const { router } = renderAt("/invite#tok3n");

  await vi.waitFor(() => expect(router.state.location.pathname).toBe(`/app/orgs/${ORG_ID}`));
  expect(fake.requests.filter((request) => request.method === "POST")).toHaveLength(1);
});

test("an invitation that can't be used says why", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    [ACCEPT]: {
      status: 409,
      body: {
        title: "Conflict",
        detail: "This invitation is for a different email address.",
        trace_id: "i1",
      },
    },
  });

  renderAt("/invite#tok3n");

  expect(
    await screen.findByRole("heading", { name: "This invitation can't be used" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "This invitation is for a different email address. (reference i1)",
  );
});
