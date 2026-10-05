import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { ADMIN, FINDING_ID, ORG_ID, OWNER, VIEWER } from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const AUDIT = `GET ${ORG}/audit-log`;
const PAGE = `/app/orgs/${ORG_ID}/audit`;

function auditEvent(id: number, fields: object) {
  return {
    id: `01a10900-0000-7000-8000-00000000000${id}`,
    created_at: `2026-10-04T10:0${id}:00Z`,
    actor_user_id: OWNER.user_id,
    actor_type: "user",
    action: "org.renamed",
    target_type: "organization",
    target_id: ORG_ID,
    outcome: "success",
    details: {},
    ...fields,
  };
}

test("an owner reads the audit log, newest first, with each action in words", async () => {
  signedInAs(OWNER, {
    [AUDIT]: {
      body: {
        events: [
          auditEvent(3, {
            actor_user_id: ADMIN.user_id,
            action: "finding.status_changed",
            target_type: "finding",
            target_id: FINDING_ID,
            details: { from: "open", to: "resolved" },
          }),
          auditEvent(2, { actor_user_id: null, actor_type: "system", action: "budget.exhausted" }),
          auditEvent(1, { action: "member.role_changed", outcome: "denied" }),
        ],
        next_cursor: null,
      },
    },
  });

  renderAt(PAGE);

  const rows = (await screen.findAllByRole("row")).slice(1);
  expect(rows).toHaveLength(3);
  const [triage, budget, refused] = rows as [HTMLElement, HTMLElement, HTMLElement];
  expect(await within(triage).findByText("Ben Admin")).toBeInTheDocument();
  expect(within(triage).getByText("Changed a finding's status")).toBeInTheDocument();
  expect(within(triage).getByText("finding.status_changed")).toBeInTheDocument();
  expect(within(triage).getByText("from: open, to: resolved")).toBeInTheDocument();
  expect(within(triage).getByRole("link", { name: "The finding" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`,
  );
  expect(within(budget).getByText("NetTriage")).toBeInTheDocument();
  expect(within(refused).getByText("Refused")).toBeInTheDocument();
  expect(screen.getByText("Events are kept for 180 days.")).toBeInTheDocument();
  expect(document.title).toBe("Audit log · Acme Security · NetTriage");
});

test("an action this version of the app doesn't know is shown by its code", async () => {
  signedInAs(OWNER, {
    [AUDIT]: {
      body: { events: [auditEvent(1, { action: "report.exported" })], next_cursor: null },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByText("report.exported")).toBeInTheDocument();
});

test("older events load a page at a time", async () => {
  signedInAs(OWNER, {
    [AUDIT]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { events: [auditEvent(1, { action: "org.created" })], next_cursor: null } }
        : { body: { events: [auditEvent(2, {})], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show older events" }));

  expect(await screen.findByText("Created the organization")).toBeInTheDocument();
  expect(screen.getByText("Renamed the organization")).toBeInTheDocument();
});

test("only owners and admins see the audit log, in the tabs and at its address", async () => {
  const fake = signedInAs({ ...VIEWER, role: "analyst" });

  renderAt(PAGE);

  expect(
    await screen.findByText("Only Owners and Admins can see the audit log."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Audit log" })).toBeNull();
  expect(fake.requests.some((request) => request.url.includes("/audit-log"))).toBe(false);
});

test("an audit log the API refuses says why", async () => {
  signedInAs(OWNER, {
    [AUDIT]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t8" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t8)");
});
