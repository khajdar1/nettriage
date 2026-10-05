import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ADMIN, FINDING_ID, ORG_ID, OWNER, VIEWER, finding } from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";

const FINDING = `${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;

function event(id: number, type: string, actor: string | null, payload: Record<string, unknown>) {
  return {
    id: `01a10700-0000-7000-8000-00000000000${id}`,
    type,
    actor_id: actor,
    payload,
    created_at: `2026-10-04T09:3${id}:00Z`,
  };
}

test("a contributor changes the status and assignee, naming the version they read", async () => {
  let patch: Request | undefined;
  const changed = finding({ status: "investigating", assignee_id: ADMIN.user_id, version: 2 });
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [`PATCH ${FINDING}`]: (request) => {
      patch = request;
      return { body: changed, headers: { ETag: '"2"' } };
    },
  });
  const { user } = renderAt(PAGE);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Status" }),
    "Investigating",
  );
  await user.selectOptions(screen.getByRole("combobox", { name: "Assignee" }), "Ben Admin");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await vi.waitFor(() => expect(patch).toBeDefined());
  expect(patch?.headers.get("If-Match")).toBe('"1"');
  expect(await patch?.json()).toEqual({ status: "investigating", assignee_id: ADMIN.user_id });
  expect(await screen.findByRole("button", { name: "Save changes" })).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("investigating");
});

test("only what changed is sent, and unassigning sends no one", async () => {
  let body: unknown;
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ assignee_id: ADMIN.user_id }) },
    [`PATCH ${FINDING}`]: async (request) => {
      body = await request.json();
      return { body: finding({ version: 2 }) };
    },
  });
  const { user } = renderAt(PAGE);

  await user.selectOptions(await screen.findByRole("combobox", { name: "Assignee" }), "Unassigned");
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await vi.waitFor(() => expect(body).toEqual({ assignee_id: null }));
});

test("a change someone else made first is shown, and nothing is overwritten", async () => {
  let reads = 0;
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: () =>
      ++reads === 1 ? { body: finding() } : { body: finding({ status: "resolved", version: 2 }) },
    [`PATCH ${FINDING}`]: {
      status: 412,
      headers: { ETag: '"2"' },
      body: { title: "Precondition Failed", detail: "The finding changed.", trace_id: "t4" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.selectOptions(
    await screen.findByRole("combobox", { name: "Status" }),
    "False positive",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Someone changed this finding while you had it open. It now shows their change; make yours again if it still applies.",
  );
  expect(await screen.findByRole("combobox", { name: "Status" })).toHaveValue("resolved");
});

test("only owners, admins and analysts can be assigned", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { body: finding() } });

  renderAt(PAGE);

  const assignee = await screen.findByRole("combobox", { name: "Assignee" });
  await vi.waitFor(() =>
    expect(
      within(assignee)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Unassigned", "ana@example.com", "Ben Admin"]),
  );
});

test("a viewer sees the status and assignee, with nothing to change", async () => {
  signedInAs(VIEWER, {
    [`GET ${FINDING}`]: { body: finding({ status: "investigating", assignee_id: ADMIN.user_id }) },
  });

  renderAt(PAGE);

  const triage = await screen.findByRole("region", { name: "Triage" });
  expect(within(triage).getByText("Investigating")).toBeInTheDocument();
  expect(await within(triage).findByText("Ben Admin")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByLabelText("Add a comment")).toBeNull();
});

test("the history reads as sentences, oldest first, with comments as they were written", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        events: [
          event(1, "created", null, {}),
          event(2, "status_changed", ADMIN.user_id, { from: "open", to: "investigating" }),
          event(3, "assigned", ADMIN.user_id, { from: null, to: ADMIN.user_id }),
          event(4, "commented", OWNER.user_id, { text: "Checked: our own scanner.\nClosing." }),
        ],
        events_total: 4,
      }),
    },
  });

  renderAt(PAGE);

  const activity = await screen.findByRole("region", { name: "Activity" });
  await vi.waitFor(() =>
    expect(
      within(activity)
        .getAllByRole("listitem")
        .map((item) => item.firstChild?.textContent),
    ).toEqual([
      "NetTriage found it",
      "Ben Admin changed the status from Open to Investigating",
      "Ben Admin took it on",
      "ana@example.com commented",
    ]),
  );
  expect(within(activity).getByText(/Checked: our own scanner\./)).toHaveTextContent(
    "Checked: our own scanner. Closing.",
  );
});

test("a comment joins the history, sent once", async () => {
  let reads = 0;
  const fake = signedInAs(OWNER, {
    [`GET ${FINDING}`]: () =>
      ++reads === 1
        ? { body: finding() }
        : {
            body: finding({
              events: [
                event(1, "created", null, {}),
                event(2, "commented", OWNER.user_id, { text: "Looking." }),
              ],
              events_total: 2,
            }),
          },
    [`POST ${FINDING}/comments`]: {
      status: 201,
      body: event(2, "commented", OWNER.user_id, { text: "Looking." }),
    },
  });
  const { user } = renderAt(PAGE);

  await user.type(await screen.findByLabelText("Add a comment"), "Looking.");
  await user.click(screen.getByRole("button", { name: "Comment" }));

  expect(await screen.findByText("Looking.")).toBeInTheDocument();
  expect(screen.getByLabelText("Add a comment")).toHaveValue("");
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.json()).toEqual({ text: "Looking." });
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
});

test("a long history says how much of it is shown", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { body: finding({ events_total: 140 }) } });

  renderAt(PAGE);

  expect(await screen.findByText("Showing the latest 1 of 140 events.")).toBeInTheDocument();
});

test("a comment the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [`POST ${FINDING}/comments`]: {
      status: 429,
      headers: { "Retry-After": "30" },
      body: { title: "Too Many Requests" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.type(await screen.findByLabelText("Add a comment"), "Again.");
  await user.click(screen.getByRole("button", { name: "Comment" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Too many requests. Try again in 30 seconds.",
  );
  expect(screen.getByLabelText("Add a comment")).toHaveValue("Again.");
});
