import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Member } from "../../orgs/members";
import {
  ADMIN,
  FINDING_ID,
  ORG_ID,
  OWNER,
  UPLOAD_ID,
  VIEWER,
  findingSummary,
  overview,
  upload,
} from "../../test/fixtures";
import type { Handler } from "../../test/fakeApi";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const FINDINGS = `GET ${ORG}/findings`;
const PAGE = `/app/orgs/${ORG_ID}/findings`;
const NONE = { body: { findings: [], next_cursor: null } };

const NOTHING = overview({
  unresolved_by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
  by_status: { open: 0, investigating: 0, resolved: 0, false_positive: 0 },
  unresolved_unassigned: 0,
  unresolved_mine: 0,
  new_last_day: 0,
  last_upload: null,
});

/** Signed in, with the organization's overview the glance reads (Plan 6d). */
function signedIn(self: Member, extra: Record<string, Handler> = {}) {
  return signedInAs(self, { [`GET ${ORG}/overview`]: { body: overview() }, ...extra });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

function lastQuery(requests: Request[]): URLSearchParams {
  const reads = requests.filter((request) => new URL(request.url).pathname.endsWith("/findings"));
  return new URL(reads.at(-1)?.url ?? "http://x").searchParams;
}

test("an organization opens on its unresolved findings, most severe first", async () => {
  const fake = signedIn(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [
          findingSummary({
            status: "investigating",
            assignee_id: ADMIN.user_id,
            ai_status: "succeeded",
          }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(`/app/orgs/${ORG_ID}`);

  expect(await screen.findByRole("heading", { level: 1, name: "Findings" })).toBeInTheDocument();
  const row = await screen.findByRole("row", { name: /Port scan of 10\.0\.0\.5/ });
  expect(within(row).getByText("High")).toBeInTheDocument();
  expect(
    within(row).getByRole("link", {
      name: "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes",
    }),
  ).toHaveAttribute("href", `${PAGE}/${FINDING_ID}`);
  expect(within(row).getByText("Investigating")).toBeInTheDocument();
  expect(await within(row).findByText("Ben Admin")).toBeInTheDocument();
  expect(within(row).getByText("BA")).toHaveAttribute("aria-hidden", "true");
  expect(within(row).getByText("Port scan")).toBeInTheDocument();
  expect(within(row).getByText("10.0.3.17 → 10.0.0.5")).toBeInTheDocument();
  expect(within(row).getByText("Explained")).toBeInTheDocument();
  expect(within(row).getByText("yesterday")).toHaveAttribute("dateTime", "2026-10-04T09:31:00Z");
  expect(screen.getByRole("columnheader", { name: "Detected" })).toBeInTheDocument();
  expect(lastQuery(fake.requests).get("sort")).toBe("severity");
  expect(lastQuery(fake.requests).getAll("status")).toEqual(["open", "investigating"]);
  expect(screen.getByLabelText("Status")).toHaveDisplayValue("Unresolved");
  expect(screen.getByRole("link", { name: "Findings" })).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Findings · Acme Security · NetTriage");
});

test("a finding assigned to the signed-in person says You, and one nobody has says Unassigned", async () => {
  signedIn(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [
          findingSummary({ assignee_id: OWNER.user_id }),
          findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "RDP attempts" }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const mine = await screen.findByRole("row", { name: /Port scan of 10\.0\.0\.5/ });
  expect(await within(mine).findByText("You")).toBeInTheDocument();
  const nobody = screen.getByRole("row", { name: /RDP attempts/ });
  expect(within(nobody).getByText("Unassigned")).toBeInTheDocument();
});

test("filters and the order are kept in the address and sent to the API", async () => {
  const fake = signedIn(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
  });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Severity"), "High");
  await user.selectOptions(screen.getByLabelText("Status"), "Investigating");
  await user.selectOptions(screen.getByLabelText("Assignee"), "Yours");
  await user.selectOptions(screen.getByLabelText("Detector"), "SSH/RDP brute force");
  await user.selectOptions(screen.getByLabelText("Sort"), "Newest first");

  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("newest"));
  const sent = lastQuery(fake.requests);
  expect([sent.getAll("status"), sent.get("severity"), sent.get("assignee")]).toEqual([
    ["investigating"],
    "high",
    "me",
  ]);
  expect(sent.get("detector")).toBe("remote_access_bruteforce");
  expect(router.state.location.search).toBe(
    "?severity=high&status=investigating&assignee=me&detector=remote_access_bruteforce&sort=newest",
  );
});

test("any status asks for every finding, and the address says so", async () => {
  const fake = signedIn(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Status"), "Any status");

  await vi.waitFor(() => expect(router.state.location.search).toBe("?status=any"));
  await vi.waitFor(() => expect(lastQuery(fake.requests).has("status")).toBe(false));
});

test("new in the last day asks the API for findings since 24 hours ago", async () => {
  const fake = signedIn(OWNER, { [FINDINGS]: NONE });

  const { user, router } = renderAt(`${PAGE}?status=any&new=day`);

  // vi.waitFor moves the faked clock on a little each time it looks.
  await vi.waitFor(() =>
    expect(lastQuery(fake.requests).get("since")).toMatch(/^2026-10-04T12:00:0/),
  );
  expect(lastQuery(fake.requests).has("status")).toBe(false);
  expect(screen.getByText(/Detected in the last day./)).toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: "Show findings from any time" }));

  expect(router.state.location.search).toBe("?status=any");
});

test("an address with values that aren't real is read as no filter", async () => {
  const fake = signedIn(OWNER, { [FINDINGS]: NONE });

  renderAt(`${PAGE}?severity=urgent&status=closed&assignee=bob&new=week&sort=oldest`);

  await screen.findByRole("heading", { level: 1, name: "Findings" });
  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("severity"));
  const sent = lastQuery(fake.requests);
  expect(["severity", "assignee", "since"].filter((name) => sent.has(name))).toEqual([]);
  expect(sent.getAll("status")).toEqual(["open", "investigating"]);
  expect(screen.getByLabelText("Severity")).toHaveValue("");
  expect(screen.getByLabelText("Status")).toHaveDisplayValue("Unresolved");
  expect(screen.getByLabelText("Assignee")).toHaveDisplayValue("Anyone");
});

test("findings from one upload name it, with a way back to every upload's findings", async () => {
  const fake = signedIn(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { body: upload() },
  });
  const { user, router } = renderAt(`${PAGE}?upload=${UPLOAD_ID}`);

  expect((await screen.findByText("port-scan.log")).closest("p")).toHaveTextContent(
    "Findings from port-scan.log.",
  );
  expect(lastQuery(fake.requests).get("upload")).toBe(UPLOAD_ID);
  await user.click(screen.getByRole("link", { name: "Show every upload's findings" }));

  expect(router.state.location.search).toBe("");
});

test("with no findings yet, a contributor is pointed to uploading", async () => {
  signedIn(OWNER, { [FINDINGS]: NONE, [`GET ${ORG}/overview`]: { body: NOTHING } });

  renderAt(PAGE);

  expect(await screen.findByText("No findings yet.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Upload a flow log" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/uploads`,
  );
});

test("with no findings yet, a viewer is told where they come from", async () => {
  signedIn(VIEWER, { [FINDINGS]: NONE, [`GET ${ORG}/overview`]: { body: NOTHING } });

  renderAt(PAGE);

  expect(
    await screen.findByText("Findings appear here once a flow log has been analyzed."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Upload a flow log" })).toBeNull();
});

test("with nothing unresolved, the person is offered every finding", async () => {
  signedIn(OWNER, {
    [FINDINGS]: NONE,
    [`GET ${ORG}/overview`]: {
      body: overview({
        ...NOTHING,
        by_status: { open: 0, investigating: 0, resolved: 4, false_positive: 1 },
      }),
    },
  });
  const { user, router } = renderAt(PAGE);

  expect(await screen.findByText("Nothing here is unresolved.")).toBeInTheDocument();
  await user.click(screen.getByRole("link", { name: "Show every finding" }));

  expect(router.state.location.search).toBe("?status=any");
});

test("no finding matching the filters offers to clear them", async () => {
  signedIn(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(`${PAGE}?severity=low&sort=newest`);

  expect(await screen.findByText("No findings match these filters.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));

  expect(router.state.location.search).toBe("?sort=newest");
});

test("more findings load a page at a time, under the same filters", async () => {
  const older = findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "Older scan" });
  const fake = signedIn(OWNER, {
    [FINDINGS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { findings: [older], next_cursor: null } }
        : { body: { findings: [findingSummary()], next_cursor: "c2" } },
  });
  const { user } = renderAt(`${PAGE}?assignee=none`);

  await user.click(await screen.findByRole("button", { name: "Show more findings" }));

  expect(await screen.findByRole("link", { name: "Older scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show more findings" })).toBeNull();
  const second = lastQuery(fake.requests);
  expect([second.get("cursor"), second.get("assignee")]).toEqual(["c2", "none"]);
  expect(second.getAll("status")).toEqual(["open", "investigating"]);
});

test("an overview the API refuses says why, and the findings are still listed", async () => {
  signedIn(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
    [`GET ${ORG}/overview`]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t9" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t9)");
  expect(await screen.findByRole("row", { name: /Port scan of 10\.0\.0\.5/ })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Findings at a glance" })).toBeNull();
});

test("a list the API refuses says why", async () => {
  signedIn(OWNER, {
    [FINDINGS]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t7" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t7)");
});
