import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  ADMIN,
  EXPLANATION,
  FINDING_ID,
  ORG_ID,
  OWNER,
  UPLOAD_ID,
  aiAnalysis,
  finding,
  upload,
} from "../../../test/fixtures";
import type { Handler } from "../../../test/fakeApi";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";
import { formatTime } from "../../../ui/format";
import { aiVerdict } from "./SummaryStrip";

const FINDING = `GET ${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;

function opening(extra: Record<string, Handler>) {
  return signedInAs(OWNER, {
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { body: upload() },
    "GET /api/v1/attack-techniques/T1046": { status: 404, body: { title: "Not Found" } },
    ...extra,
  });
}

async function strip(): Promise<HTMLElement> {
  return (await screen.findByText("Detected")).closest("dl") as HTMLElement;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

test("under its title, a finding says where it stands in one line", async () => {
  opening({
    [FINDING]: {
      body: finding({
        status: "investigating",
        assignee_id: ADMIN.user_id,
        ai_analysis: aiAnalysis(),
      }),
    },
  });

  renderAt(PAGE);

  const summary = await strip();
  const term = (name: string) => within(summary).getByText(name).nextElementSibling;
  expect(term("Status")).toHaveTextContent("Investigating");
  expect(await within(summary).findByText("Ben Admin")).toBeInTheDocument();
  expect(term("Detected")).toHaveTextContent("yesterday");
  expect(term("Window")).toHaveTextContent(
    `${formatTime("2026-10-01T12:00:00Z")} to ${formatTime("2026-10-01T12:05:00Z")}`,
  );
  expect(await within(summary).findByRole("link", { name: "port-scan.log" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?status=any&upload=${UPLOAD_ID}`,
  );
  expect(term("AI")).toHaveTextContent("Agrees: High, medium confidence");
});

test("a finding nobody has taken on says Unassigned; one the person has says You", async () => {
  opening({ [FINDING]: { body: finding() } });
  renderAt(PAGE);
  expect(within(await strip()).getByText("Unassigned")).toBeInTheDocument();
});

test("the signed-in person's own finding says You", async () => {
  opening({ [FINDING]: { body: finding({ assignee_id: OWNER.user_id }) } });
  renderAt(PAGE);
  expect(await within(await strip()).findByText("You")).toBeInTheDocument();
});

test("an upload that can't be read leaves the strip without it", async () => {
  opening({
    [FINDING]: { body: finding() },
    [`GET ${ORG}/uploads/${UPLOAD_ID}`]: { status: 404, body: { title: "Not Found" } },
  });

  renderAt(PAGE);

  const summary = await strip();
  await vi.waitFor(() => expect(within(summary).getByText("AI")).toBeInTheDocument());
  expect(within(summary).queryByText("Upload")).toBeNull();
});

test("the AI's verdict reads from its latest analysis", () => {
  const disagrees = {
    ...EXPLANATION,
    severity_assessment: {
      agrees_with_detector: false,
      suggested_severity: "medium",
      reason: "External, slow.",
    },
  };

  expect(aiVerdict(null, "high")).toBe("Not explained yet");
  expect(aiVerdict(aiAnalysis({ status: "pending", output: null }), "high")).toBe("Queued");
  expect(aiVerdict(aiAnalysis(), "critical")).toBe("Agrees: Critical, medium confidence");
  expect(aiVerdict(aiAnalysis({ output: disagrees }), "high")).toBe("Suggests Medium");
  for (const status of ["failed", "skipped_budget", "invalid_output"] as const) {
    expect(aiVerdict(aiAnalysis({ status, output: null }), "high")).toBe("No explanation");
  }
  expect(aiVerdict(aiAnalysis({ output: { summary: "half an answer" } }), "high")).toBe(
    "No explanation",
  );
});
