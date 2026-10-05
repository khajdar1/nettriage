import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ORG_ID, OWNER, UPLOAD_ID, overview, upload } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const PAGE = `/app/orgs/${ORG_ID}/findings`;
const NONE = { body: { findings: [], next_cursor: null } };

function showing(body = overview()) {
  return signedInAs(OWNER, {
    [`GET ${ORG}/findings`]: NONE,
    [`GET ${ORG}/overview`]: { body },
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("the heading counts the unresolved findings among all of them", async () => {
  showing();

  renderAt(PAGE);

  expect(await screen.findByText("14 unresolved of 45")).toBeInTheDocument();
});

test("each severity's tile counts its unresolved findings and lists them", async () => {
  showing();
  const { user, router } = renderAt(PAGE);

  const glance = await screen.findByRole("region", { name: "Findings at a glance" });
  const critical = await within(glance).findByRole("link", {
    name: "2 unresolved Critical findings",
  });
  expect(critical).toHaveClass("rung");
  expect(within(glance).getByRole("link", { name: "0 unresolved Medium findings" })).toHaveClass(
    "zero",
  );
  await user.click(critical);

  expect(router.state.location.search).toBe("?severity=critical");
});

test("quick views list yours, the unassigned and the new, and the one in use looks pressed", async () => {
  showing();

  renderAt(`${PAGE}?assignee=me`);

  const yours = await screen.findByRole("link", { name: "Yours 2" });
  expect(yours).toHaveAttribute("href", `${PAGE}?assignee=me`);
  expect(yours).toHaveAttribute("aria-current", "true");
  const unassigned = screen.getByRole("link", { name: "Unassigned 7" });
  expect(unassigned).toHaveAttribute("href", `${PAGE}?assignee=none`);
  expect(unassigned).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "New in the last day 5" })).toHaveAttribute(
    "href",
    `${PAGE}?status=any&new=day`,
  );
});

test("a quick view with other filters beside it isn't the one in use", async () => {
  showing();

  renderAt(`${PAGE}?assignee=me&severity=high`);

  const yours = await screen.findByRole("link", { name: "Yours 2" });
  expect(yours).not.toHaveAttribute("aria-current");
  // A count lists exactly what it counted: its view replaces the other filters.
  expect(yours).toHaveAttribute("href", `${PAGE}?assignee=me`);
});

test("an organization with no findings shows its counts at zero, and an empty track", async () => {
  showing(
    overview({
      unresolved_by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
      by_status: { open: 0, investigating: 0, resolved: 0, false_positive: 0 },
      unresolved_unassigned: 0,
      unresolved_mine: 0,
      new_last_day: 0,
      last_upload: null,
    }),
  );

  renderAt(PAGE);

  expect(await screen.findByText("All 0, by status")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "0 unresolved Critical findings" })).toHaveClass("zero");
  expect(document.querySelectorAll("svg.status-track > rect")).toHaveLength(0);
});

test("the status track splits every finding by status, and its legend links to each", async () => {
  showing();

  renderAt(PAGE);

  expect(await screen.findByText("All 45, by status")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "9 Open findings" })).toHaveAttribute(
    "href",
    `${PAGE}?status=open`,
  );
  expect(screen.getByRole("link", { name: "3 False positive findings" })).toHaveAttribute(
    "href",
    `${PAGE}?status=false_positive`,
  );
  const track = document.querySelector("svg.status-track");
  expect(track).toHaveAttribute("aria-hidden", "true");
  const widths = Array.from(track?.querySelectorAll(":scope > rect") ?? []).map((rect) => [
    rect.getAttribute("class"),
    rect.getAttribute("x"),
    rect.getAttribute("width"),
  ]);
  expect(widths).toEqual([
    ["track-open", "0%", "20%"],
    ["track-investigating", "20%", "11.111%"],
    ["track-resolved", "31.111%", "62.222%"],
    ["track-false-positive", "93.333%", "6.667%"],
  ]);
});

test("the last upload is named, with how long ago it came and how many findings it had", async () => {
  showing(
    overview({
      last_upload: upload({
        created_at: "2026-10-05T10:00:00Z",
        findings: 6,
        worst_severity: "high",
      }),
    }),
  );

  renderAt(PAGE);

  const link = await screen.findByRole("link", { name: "port-scan.log" });
  expect(link).toHaveAttribute("href", `${PAGE}?status=any&upload=${UPLOAD_ID}`);
  expect(link.closest("p")).toHaveTextContent("Last upload port-scan.log, 2 h ago: 6 findings.");
});

test("an upload still being analyzed says so instead of its findings", async () => {
  showing(
    overview({ last_upload: upload({ status: "processing", created_at: "2026-10-05T11:58:00Z" }) }),
  );

  renderAt(PAGE);

  expect(
    (await screen.findByRole("link", { name: "port-scan.log" })).closest("p"),
  ).toHaveTextContent("Last upload port-scan.log, 2 min ago: analyzing.");
});

test("with no uploads, the glance says so", async () => {
  showing(overview({ last_upload: null }));

  renderAt(PAGE);

  expect(await screen.findByText("No uploads yet.")).toBeInTheDocument();
});
