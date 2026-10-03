import { act, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ME_KEY, browser } from "../auth/session";
import { fakeApi } from "../test/fakeApi";
import { ME } from "../test/fixtures";
import { renderAt } from "../test/render";

const LOGOUT_URL = "https://auth.example.com/logout?client_id=abc";

test("settings show who is signed in", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/app/settings");

  expect(
    await screen.findByRole("heading", { level: 1, name: "Account settings" }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("ana@example.com").length).toBeGreaterThan(0);
  expect(document.title).toBe("Account settings · NetTriage");
});

test("sign out everywhere ends every session, then Cognito's", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/auth/logout-all": { body: { logout_url: LOGOUT_URL } },
  });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  const { user } = renderAt("/app/settings");

  await user.click(await screen.findByRole("button", { name: "Sign out everywhere" }));

  await vi.waitFor(() => expect(go).toHaveBeenCalledWith(LOGOUT_URL));
  expect(fake.requests.some((request) => request.url.endsWith("/api/auth/logout-all"))).toBe(true);
});

test("sign out, on every page, ends this session", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/auth/logout": { body: { logout_url: LOGOUT_URL } },
  });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  const { user } = renderAt("/app");

  await user.click(await screen.findByRole("button", { name: "Sign out" }));

  await vi.waitFor(() => expect(go).toHaveBeenCalledWith(LOGOUT_URL));
});

test("a sign-out that fails says why and keeps the person here", async () => {
  fakeApi({
    "GET /api/v1/me": { body: ME },
    "POST /api/auth/logout-all": {
      status: 503,
      body: { title: "Service Unavailable", detail: "Signing out is unavailable.", trace_id: "t1" },
    },
  });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  const { user } = renderAt("/app/settings");

  await user.click(await screen.findByRole("button", { name: "Sign out everywhere" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Signing out is unavailable. (reference t1)",
  );
  expect(go).not.toHaveBeenCalled();
});

test("a session check that fails in the background keeps the page as it is", async () => {
  let reads = 0;
  fakeApi({
    "GET /api/v1/me": () =>
      ++reads === 1 ? { body: ME } : { status: 503, body: { title: "Service Unavailable" } },
  });
  const { client } = renderAt("/app/settings");
  await screen.findByRole("heading", { level: 1, name: "Account settings" });

  await act(() => client.refetchQueries({ queryKey: ME_KEY }));
  // TanStack Query tells React on its next tick; let that happen before looking.
  await act(() => new Promise((resolve) => setTimeout(resolve, 10)));

  expect(reads).toBe(2);
  expect(screen.getByRole("heading", { level: 1, name: "Account settings" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
