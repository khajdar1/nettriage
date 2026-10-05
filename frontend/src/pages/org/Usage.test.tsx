import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ORG_ID, OWNER, VIEWER } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const USAGE = `GET ${ORG}/usage`;
const PAGE = `/app/orgs/${ORG_ID}/usage`;
const SOME = {
  body: {
    days: [
      { day: "2026-10-04", calls: 3, input_tokens: 2700, output_tokens: 900, cost_usd: "0.0009" },
      { day: "2026-10-02", calls: 1, input_tokens: 900, output_tokens: 300, cost_usd: "0.0003" },
    ],
    totals: { calls: 4, input_tokens: 3600, output_tokens: 1200, cost_usd: "0.0012" },
  },
};

function asked(requests: Request[]): string | null | undefined {
  const reads = requests.filter((request) => request.url.includes("/usage"));
  return reads.length === 0 ? undefined : new URL(reads.at(-1)?.url ?? "").searchParams.get("days");
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-04T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("an owner sees the AI's cost per day drawn, then listed, with totals", async () => {
  const fake = signedInAs(OWNER, { [USAGE]: SOME });

  renderAt(PAGE);

  const chart = await screen.findByRole("figure", { name: /AI cost per UTC day/ });
  expect(chart.querySelectorAll("rect.bar")).toHaveLength(2);
  const table = screen.getByRole("table", { name: /AI calls by day/ });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(4);
  expect(rows[1]).toHaveTextContent("3");
  expect(rows[3]).toHaveTextContent("Total");
  expect(rows[3]).toHaveTextContent("$0.0012");
  expect(asked(fake.requests)).toBe("30");
  expect(document.title).toBe("AI usage · Acme Security · NetTriage");
});

test("choosing a period asks for it, and keeps it in the address", async () => {
  const fake = signedInAs(OWNER, { [USAGE]: SOME });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Period"), "Last 7 days");

  await vi.waitFor(() => expect(asked(fake.requests)).toBe("7"));
  expect(router.state.location.search).toBe("?days=7");
});

test("a period without AI calls says so", async () => {
  signedInAs(OWNER, {
    [USAGE]: {
      body: { days: [], totals: { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: "0" } },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByText("No AI calls in the last 30 days.")).toBeInTheDocument();
  expect(screen.queryByRole("figure")).toBeNull();
});

test("only owners and admins see the AI usage, in the tabs and at its address", async () => {
  const fake = signedInAs({ ...VIEWER, role: "analyst" });

  renderAt(PAGE);

  expect(
    await screen.findByText("Only Owners and Admins can see the AI usage."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "AI usage" })).toBeNull();
  expect(asked(fake.requests)).toBeUndefined();
});
