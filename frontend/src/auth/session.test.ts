import { afterEach, expect, test, vi } from "vitest";
import { api, setCsrfToken } from "../api/client";
import { fakeApi } from "../test/fakeApi";
import { ME, SIGNED_OUT } from "../test/fixtures";
import { browser, fetchMe, signInUrl, signOut } from "./session";

afterEach(() => setCsrfToken(null));

async function csrfSentByAPost(): Promise<string | null> {
  const fake = fakeApi({ "POST /api/v1/orgs": { status: 201, body: {} } });
  await api.POST("/api/v1/orgs", { body: { name: "Acme Security" } });
  return fake.requests[0]?.headers.get("X-CSRF-Token") ?? null;
}

test("the signed-in person comes with the CSRF token later requests send", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  expect(await fetchMe()).toEqual(ME);
  expect(await csrfSentByAPost()).toBe("csrf-1");
});

test("signed out is null, and forgets the CSRF token", async () => {
  setCsrfToken("old");
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  expect(await fetchMe()).toBeNull();
  expect(await csrfSentByAPost()).toBeNull();
});

test("signing in starts at the API and comes back to the page asked for", () => {
  expect(signInUrl("/app/orgs/1/members")).toBe(
    "/api/auth/login?return_to=%2Fapp%2Forgs%2F1%2Fmembers",
  );
});

test.each([
  [false, "/api/auth/logout"],
  [true, "/api/auth/logout-all"],
])("signing out (everywhere: %s) ends the session, then Cognito's", async (everywhere, path) => {
  const logoutUrl = "https://auth.example.com/logout?client_id=abc";
  const fake = fakeApi({ [`POST ${path}`]: { body: { logout_url: logoutUrl } } });
  const go = vi.spyOn(browser, "go").mockImplementation(() => undefined);
  setCsrfToken("csrf-1");

  await signOut({ everywhere });

  expect(fake.requests[0]?.headers.get("X-CSRF-Token")).toBe("csrf-1");
  expect(go).toHaveBeenCalledWith(logoutUrl);
  expect(await csrfSentByAPost()).toBeNull();
});
