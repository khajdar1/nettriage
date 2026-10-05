import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fakeApi } from "../../test/fakeApi";
import { ACME, ORG_ID, findingSummary, memberOf, overview, upload } from "../../test/fixtures";
import { renderAt } from "../../test/render";

const LAB_ID = "01a10333-0000-7000-8000-000000000002";
const LAB = {
  ...ACME,
  org_id: LAB_ID,
  name: "Northwind Labs",
  slug: "northwind-labs",
  role: "analyst" as const,
};
const NONE = { body: { findings: [], next_cursor: null } };

function finding(id: number, fields: object) {
  return findingSummary({ id: `01a10600-0000-7000-8000-00000000000${id}`, ...fields });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("findings assigned to the person in every organization are listed together, worst first", async () => {
  const fake = fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME, LAB) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: {
      body: {
        findings: [
          finding(1, { severity: "high", title: "Port scan of 10.0.0.5" }),
          finding(2, {
            severity: "medium",
            title: "RDP attempts",
            created_at: "2026-10-05T09:00:00Z",
          }),
        ],
        next_cursor: null,
      },
    },
    [`GET /api/v1/orgs/${LAB_ID}/findings`]: {
      body: {
        findings: [
          finding(3, { severity: "critical", title: "SSH brute force", status: "investigating" }),
        ],
        next_cursor: null,
      },
    },
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview() },
    [`GET /api/v1/orgs/${LAB_ID}/overview`]: { body: overview() },
  });

  renderAt("/app");

  const assigned = await screen.findByRole("region", { name: "Assigned to you" });
  await vi.waitFor(() => expect(within(assigned).getAllByRole("row")).toHaveLength(4));
  const rows = within(assigned).getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getByRole("link").textContent)).toEqual([
    "SSH brute force",
    "Port scan of 10.0.0.5",
    "RDP attempts",
  ]);
  expect(within(rows[0] as HTMLElement).getByText("Northwind Labs")).toBeInTheDocument();
  expect(within(rows[0] as HTMLElement).getByRole("link")).toHaveAttribute(
    "href",
    `/app/orgs/${LAB_ID}/findings/01a10600-0000-7000-8000-000000000003`,
  );
  expect(within(rows[2] as HTMLElement).getByText("3 h ago")).toBeInTheDocument();
  expect(within(assigned).getByText("3 unresolved, in 2 organizations")).toBeInTheDocument();
  const asked = new URL(
    fake.requests.find((request) => request.url.includes(`${ORG_ID}/findings`))?.url ?? "http://x",
  ).searchParams;
  expect(asked.getAll("status")).toEqual(["open", "investigating"]);
  expect([asked.get("assignee"), asked.get("sort"), asked.get("limit")]).toEqual([
    "me",
    "severity",
    "20",
  ]);
  expect(document.title).toBe("Home · NetTriage");
});

test("an organization with more assigned than shown links to the rest", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: {
      body: { findings: [finding(1, {})], next_cursor: "c2" },
    },
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview({ unresolved_mine: 6 }) },
  });

  renderAt("/app");

  expect(await screen.findByRole("link", { name: "5 more in Acme Security" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?assignee=me`,
  );
});

test("with nothing assigned, the person is told where to find work", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: NONE,
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview() },
  });

  renderAt("/app");

  expect(
    await screen.findByText(
      "Nothing is assigned to you. Pick up an unassigned finding in one of your organizations.",
    ),
  ).toBeInTheDocument();
});

test("each organization's card shows where its findings stand, and links to them", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: NONE,
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: {
      body: overview({ last_upload: upload({ created_at: "2026-10-05T10:00:00Z" }) }),
    },
  });

  renderAt("/app");

  const card = await screen.findByRole("article", { name: "Acme Security" });
  expect(within(card).getByRole("link", { name: "Acme Security" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}`,
  );
  expect(within(card).getByText("Owner")).toBeInTheDocument();
  const critical = await within(card).findByRole("link", {
    name: "2 unresolved Critical findings",
  });
  expect(critical).toHaveAttribute("href", `/app/orgs/${ORG_ID}/findings?severity=critical`);
  expect(within(card).getByRole("link", { name: "0 unresolved Medium findings" })).toHaveClass(
    "zero",
  );
  const fact = (name: string) => within(card).getByText(name).nextElementSibling;
  expect(fact("Unassigned")).toHaveTextContent("7");
  expect(fact("Yours")).toHaveTextContent("2");
  expect(fact("Members")).toHaveTextContent("4");
  expect(fact("Last upload")).toHaveTextContent("2 h ago, analyzed");
  expect(within(card).getByText("31 resolved or false positive")).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "Open findings" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings`,
  );
});

test("an organization with no uploads yet says so on its card", async () => {
  fakeApi({
    "GET /api/v1/me": { body: memberOf(ACME) },
    [`GET /api/v1/orgs/${ORG_ID}/findings`]: NONE,
    [`GET /api/v1/orgs/${ORG_ID}/overview`]: { body: overview({ last_upload: null }) },
  });

  renderAt("/app");

  const card = await screen.findByRole("article", { name: "Acme Security" });
  await vi.waitFor(() =>
    expect(within(card).getByText("Last upload").nextElementSibling).toHaveTextContent("None yet"),
  );
});
