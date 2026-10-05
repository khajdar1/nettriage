import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import {
  ADMIN,
  FINDING_ID,
  ORG_ID,
  OWNER,
  UPLOAD_ID,
  VIEWER,
  findingSummary,
  upload,
} from "../../test/fixtures";
import { ORG, signedInAs } from "../../test/orgApi";
import { renderAt } from "../../test/render";

const FINDINGS = `GET ${ORG}/findings`;
const PAGE = `/app/orgs/${ORG_ID}/findings`;
const NONE = { body: { findings: [], next_cursor: null } };

function lastQuery(requests: Request[]): URLSearchParams {
  const reads = requests.filter((request) => new URL(request.url).pathname.endsWith("/findings"));
  return new URL(reads.at(-1)?.url ?? "http://x").searchParams;
}

test("an organization opens on its findings, most severe first", async () => {
  const fake = signedInAs(OWNER, {
    [FINDINGS]: {
      body: {
        findings: [findingSummary({ status: "investigating", assignee_id: ADMIN.user_id })],
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
  expect(lastQuery(fake.requests).get("sort")).toBe("severity");
  expect(screen.getByRole("link", { name: "Findings" })).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Findings · Acme Security · NetTriage");
});

test("filters and the order are kept in the address and sent to the API", async () => {
  const fake = signedInAs(OWNER, {
    [FINDINGS]: { body: { findings: [findingSummary()], next_cursor: null } },
  });
  const { user, router } = renderAt(PAGE);

  await user.selectOptions(await screen.findByLabelText("Severity"), "High");
  await user.selectOptions(screen.getByLabelText("Status"), "Investigating");
  await user.selectOptions(screen.getByLabelText("Detector"), "SSH/RDP brute force");
  await user.selectOptions(screen.getByLabelText("Sort"), "Newest first");

  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("newest"));
  const sent = lastQuery(fake.requests);
  expect([sent.get("severity"), sent.get("status"), sent.get("detector")]).toEqual([
    "high",
    "investigating",
    "remote_access_bruteforce",
  ]);
  expect(router.state.location.search).toBe(
    "?severity=high&status=investigating&detector=remote_access_bruteforce&sort=newest",
  );
});

test("an address with values that aren't real is read as no filter", async () => {
  const fake = signedInAs(OWNER, { [FINDINGS]: NONE });

  renderAt(`${PAGE}?severity=urgent&sort=oldest`);

  await screen.findByRole("heading", { level: 1, name: "Findings" });
  await vi.waitFor(() => expect(lastQuery(fake.requests).get("sort")).toBe("severity"));
  expect(lastQuery(fake.requests).has("severity")).toBe(false);
  expect(screen.getByLabelText("Severity")).toHaveValue("");
});

test("findings from one upload name it, with a way back to every upload's findings", async () => {
  const fake = signedInAs(OWNER, {
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
  signedInAs(OWNER, { [FINDINGS]: NONE });

  renderAt(PAGE);

  expect(await screen.findByText("No findings yet.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Upload a flow log" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/uploads`,
  );
});

test("with no findings yet, a viewer is told where they come from", async () => {
  signedInAs(VIEWER, { [FINDINGS]: NONE });

  renderAt(PAGE);

  expect(
    await screen.findByText("Findings appear here once a flow log has been analyzed."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Upload a flow log" })).toBeNull();
});

test("no finding matching the filters offers to clear them", async () => {
  signedInAs(OWNER, { [FINDINGS]: NONE });
  const { user, router } = renderAt(`${PAGE}?severity=low&sort=newest`);

  expect(await screen.findByText("No findings match these filters.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Clear filters" }));

  expect(router.state.location.search).toBe("?sort=newest");
});

test("more findings load a page at a time", async () => {
  const older = findingSummary({ id: "01a10600-0000-7000-8000-000000000002", title: "Older scan" });
  signedInAs(OWNER, {
    [FINDINGS]: (request) =>
      new URL(request.url).searchParams.get("cursor") === "c2"
        ? { body: { findings: [older], next_cursor: null } }
        : { body: { findings: [findingSummary()], next_cursor: "c2" } },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Show more findings" }));

  expect(await screen.findByRole("link", { name: "Older scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Show more findings" })).toBeNull();
});

test("a list the API refuses says why", async () => {
  signedInAs(OWNER, {
    [FINDINGS]: {
      status: 503,
      body: { title: "Service Unavailable", detail: "Try again.", trace_id: "t7" },
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again. (reference t7)");
});
