import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { ME, SIGNED_OUT } from "../test/fixtures";
import { renderAt } from "../test/render";

test("the landing page names the product and offers to sign in", async () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/");

  expect(screen.getByRole("heading", { level: 1, name: "NetTriage" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "Sign in / Sign up" })).toHaveAttribute(
    "href",
    "/api/auth/login?return_to=%2Fapp",
  );
  expect(document.title).toBe("NetTriage");
});

test("signed in, it opens the person's organizations instead", async () => {
  fakeApi({ "GET /api/v1/me": { body: ME } });

  renderAt("/");

  expect(await screen.findByRole("link", { name: "Open your organizations" })).toHaveAttribute(
    "href",
    "/app",
  );
});

test.each([
  ["expired", "Your sign-in took too long, or started in another browser. Please sign in again."],
  ["failed", "Sign-in was cancelled or didn't finish. Please try again."],
  ["disabled", "This account is disabled."],
  ["unavailable", "Sign-in isn't available right now. Please try again in a few minutes."],
  ["limited", "Too many sign-in attempts. Please wait a minute, then try again."],
])("a sign-in that failed with %s says why", (reason, message) => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt(`/?sign_in=${reason}`);

  expect(screen.getByRole("alert")).toHaveTextContent(message);
});

test.each(["<script>", "__proto__", "constructor", "toString"])(
  "a sign-in reason it doesn't know (%s) shows nothing, and the page still works",
  (reason) => {
    fakeApi({ "GET /api/v1/me": SIGNED_OUT });

    renderAt(`/?sign_in=${encodeURIComponent(reason)}`);

    expect(screen.getByRole("heading", { level: 1, name: "NetTriage" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  },
);

test("the source link opens in a new tab that can't reach back", () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/");

  const link = screen.getByRole("link", { name: "Source on GitHub" });
  expect(link).toHaveAttribute("href", "https://github.com/khajdar1/nettriage");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

test("an address that isn't a page says so and leads home", () => {
  fakeApi({ "GET /api/v1/me": SIGNED_OUT });

  renderAt("/no/such/page");

  expect(screen.getByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to the home page" })).toHaveAttribute("href", "/");
  expect(document.title).toBe("Page not found · NetTriage");
});
