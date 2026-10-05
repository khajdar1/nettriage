import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import {
  ANALYSIS_ID,
  EXPLANATION,
  FINDING_ID,
  ORG_ID,
  OWNER,
  VIEWER,
  aiAnalysis,
  finding,
} from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";

const FINDING = `${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;
const RERUN = `POST ${FINDING}/ai-analyses`;

async function panel() {
  return screen.findByRole("region", { name: "AI explanation" });
}

test("an explanation says it's AI-generated, by which model and prompt, and is shown as plain text", async () => {
  const injected = { ...EXPLANATION, summary: 'Scan. <img src="x" onerror="alert(1)">' };
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis({ output: injected }) }) },
  });

  renderAt(PAGE);

  const ai = await panel();
  expect(within(ai).getByText("AI-generated")).toBeInTheDocument();
  expect(within(ai).getByText("openai.gpt-oss-20b-1:0")).toBeInTheDocument();
  expect(within(ai).getByText(/prompt v1/)).toBeInTheDocument();
  expect(within(ai).getByText('Scan. <img src="x" onerror="alert(1)">')).toBeInTheDocument();
  expect(ai.querySelector("img")).toBeNull();
  expect(within(ai).getByRole("heading", { name: "Why it matters" })).toBeInTheDocument();
  expect(
    within(ai)
      .getAllByRole("listitem")
      .map((item) => item.textContent),
  ).toEqual([
    "An inventory or vulnerability scanner your team runs.",
    "Check what else 10.0.3.17 reached.",
    "Ask who owns 10.0.3.17.",
  ]);
  expect(within(ai).getByText(/agrees with the detector's High/)).toHaveTextContent(
    "It agrees with the detector's High. Confidence: medium.",
  );
});

test("a contributor rates an explanation, and their rating shows", async () => {
  let body: unknown;
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis() }) },
    [`PUT ${FINDING}/ai-analyses/${ANALYSIS_ID}/feedback`]: async (request) => {
      body = await request.json();
      return { body: aiAnalysis({ feedback: "up", feedback_by: OWNER.user_id }) };
    },
  });
  const { user } = renderAt(PAGE);

  await user.click(within(await panel()).getByRole("button", { name: "Useful" }));

  expect(await screen.findByRole("button", { name: "Useful" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByRole("button", { name: "Not useful" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(body).toEqual({ feedback: "up" });
});

test("asking again queues the finding, and the new explanation appears when it's ready", async () => {
  let reads = 0;
  const failed = aiAnalysis({ status: "failed", output: null, error_code: "provider_throttled" });
  const fresh = aiAnalysis({ id: "01a10800-0000-7000-8000-000000000002" });
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: () =>
      ++reads === 1
        ? { body: finding({ ai_analysis: failed }) }
        : { body: finding({ ai_analysis: fresh }) },
    [RERUN]: { status: 202, body: { status: "queued", ai_analysis: null } },
  });
  const { user } = renderAt(PAGE);

  expect(
    await within(await panel()).findByText("The AI service was busy. Try again later."),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Explain again" }));

  expect(await screen.findByText(EXPLANATION.summary)).toBeInTheDocument();
});

test("asking again when nothing changed shows the same answer, at no cost", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis() }) },
    [RERUN]: { status: 200, body: { status: "explained", ai_analysis: aiAnalysis() } },
  });
  const { user } = renderAt(PAGE);

  await user.click(within(await panel()).getByRole("button", { name: "Explain again" }));

  expect(
    await screen.findByText(
      "This explanation is current: nothing changed since it was made, so the AI wasn't asked again.",
    ),
  ).toBeInTheDocument();
});

test("a finding not explained yet offers to explain it", async () => {
  const fake = signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [RERUN]: { status: 202, body: { status: "queued", ai_analysis: null } },
  });
  const { user } = renderAt(PAGE);

  expect(await within(await panel()).findByText("Not explained yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Explain this finding" }));

  expect(
    await screen.findByText("Queued. The explanation appears here when it's ready."),
  ).toBeInTheDocument();
  const post = fake.requests.find((request) => request.method === "POST");
  expect(await post?.text()).toBe("{}");
});

test("an explanation in a newer format than this app knows is not shown", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({ ai_analysis: aiAnalysis({ output_schema_version: "v2" }) }),
    },
  });

  renderAt(PAGE);

  expect(
    await within(await panel()).findByText(
      "This explanation is in a format this version of NetTriage can't show. Reload the page to update.",
    ),
  ).toBeInTheDocument();
});

test("a viewer reads the explanation, with nothing to rate or ask", async () => {
  signedInAs(VIEWER, { [`GET ${FINDING}`]: { body: finding({ ai_analysis: aiAnalysis() }) } });

  renderAt(PAGE);

  const ai = await panel();
  expect(within(ai).getByText(EXPLANATION.summary)).toBeInTheDocument();
  expect(within(ai).queryByRole("button")).toBeNull();
});

test("a request the API refuses says why", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    [RERUN]: {
      status: 429,
      headers: { "Retry-After": "60" },
      body: { title: "Too Many Requests" },
    },
  });
  const { user } = renderAt(PAGE);

  await user.click(await screen.findByRole("button", { name: "Explain this finding" }));

  await vi.waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Too many requests. Try again in 1 minute.",
    ),
  );
});
