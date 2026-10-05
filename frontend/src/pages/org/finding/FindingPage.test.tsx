import { screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { FINDING_ID, ORG_ID, OWNER, finding } from "../../../test/fixtures";
import { ORG, signedInAs } from "../../../test/orgApi";
import { renderAt } from "../../../test/render";

const FINDING = `${ORG}/findings/${FINDING_ID}`;
const PAGE = `/app/orgs/${ORG_ID}/findings/${FINDING_ID}`;
const NOTICE =
  "Copyright 2015-2026, The MITRE Corporation. MITRE ATT&CK and ATT&CK are registered trademarks of The MITRE Corporation.";

function technique(id: string) {
  return {
    body: {
      id,
      name: "Network Service Discovery",
      tactics: ["discovery"],
      description: "",
      url: `https://attack.mitre.org/techniques/${id}/`,
      is_subtechnique: false,
      parent_id: null,
      deprecated: false,
      attack_version: "17.1",
      notice: NOTICE,
      license: "https://github.com/mitre/cti/blob/master/LICENSE.txt",
    },
  };
}

test("a finding shows its severity, title, details and numbers", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  expect(
    await screen.findByRole("heading", {
      level: 1,
      name: "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("High")).toBeInTheDocument();
  const details = screen.getByRole("region", { name: "Details" });
  const fact = (name: string) => within(details).getByText(name).nextElementSibling;
  expect(fact("Source")).toHaveTextContent("10.0.3.17");
  expect(fact("Destination")).toHaveTextContent("10.0.0.5");
  expect(fact("Port")).toHaveTextContent("Several");
  expect(fact("Protocol")).toHaveTextContent("TCP");
  expect(fact("Detector")).toHaveTextContent("Port scan, version 1");
  expect(fact("Peak in any 5 minutes")).toHaveTextContent("150");
  expect(fact("Source inside the network")).toHaveTextContent("Yes");
  expect(
    within(details).getByRole("link", { name: "Other findings from this upload" }),
  ).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings?upload=01a10500-0000-7000-8000-000000000001`,
  );
  expect(screen.getByRole("link", { name: "All findings" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings`,
  );
  expect(document.title).toBe("Port scan from 10.0.3.17 · Acme Security · NetTriage");
});

test("a port scan's evidence is mapped as the sample it is, and listed flow by flow", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  const map = await screen.findByRole("figure", {
    name: "Ports 0 to 1023 of 10.0.0.5. The scan probed 150 ports; the evidence keeps a sample.",
  });
  const lit = [...map.querySelectorAll("rect.cell.lit")].map((cell) =>
    cell.getAttribute("data-port"),
  );
  expect(lit).toEqual(["22", "80", "443"]);
  expect(within(map).getByText(/in the sample/)).toHaveTextContent("3 in the sample");
  const evidence = screen.getByRole("table", { name: /Evidence/ });
  const first = within(evidence).getAllByRole("row")[1] as HTMLElement;
  expect(within(first).getByText("10.0.3.17:40000")).toBeInTheDocument();
  expect(within(first).getByText("10.0.0.5:22")).toBeInTheDocument();
  expect(within(first).getByText("REJECT")).toBeInTheDocument();
});

test("findings other than a port scan of one host have no port map", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        detector_id: "remote_access_bruteforce",
        title: "SSH brute force against 10.0.0.5 from 203.0.113.9",
        metrics: { variant: "single", attempts: 400, hosts: 1, possible_success: false },
        techniques: [],
      }),
    },
  });

  renderAt(PAGE);

  expect(await screen.findByRole("table", { name: /Evidence/ })).toBeInTheDocument();
  expect(screen.queryByRole("figure")).toBeNull();
  expect(screen.getByText("No ATT&CK technique is linked to this finding.")).toBeInTheDocument();
});

test("ATT&CK techniques link to MITRE, say who linked them, and carry MITRE's notice", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        techniques: [
          {
            id: "T1046",
            name: "Network Service Discovery",
            url: "https://attack.mitre.org/techniques/T1046/",
            source: "detector",
            rationale: null,
          },
          {
            id: "T1595.001",
            name: "Scanning IP Blocks",
            url: "https://attack.mitre.org/techniques/T1595/001/",
            source: "ai",
            rationale: "One source tried many ports in minutes.",
          },
        ],
      }),
    },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  const techniques = await screen.findByRole("region", { name: "ATT&CK techniques" });
  const link = within(techniques).getByRole("link", { name: "T1046 Network Service Discovery" });
  expect(link).toHaveAttribute("href", "https://attack.mitre.org/techniques/T1046/");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(within(techniques).getByText("From the detector")).toBeInTheDocument();
  expect(within(techniques).getByText("Suggested by the AI")).toBeInTheDocument();
  expect(within(techniques).getByText("One source tried many ports in minutes.")).toBeVisible();
  expect(await within(techniques).findByText(NOTICE)).toBeInTheDocument();
});

test("a technique both the detector and the AI name is listed once, with both", async () => {
  signedInAs(OWNER, {
    [`GET ${FINDING}`]: {
      body: finding({
        techniques: [
          {
            id: "T1046",
            name: "Network Service Discovery",
            url: "https://attack.mitre.org/techniques/T1046/",
            source: "ai",
            rationale: "One internal host tried 150 ports on another.",
          },
          {
            id: "T1046",
            name: "Network Service Discovery",
            url: "https://attack.mitre.org/techniques/T1046/",
            source: "detector",
            rationale: null,
          },
        ],
      }),
    },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  const techniques = await screen.findByRole("region", { name: "ATT&CK techniques" });
  expect(
    within(techniques).getAllByRole("link", { name: "T1046 Network Service Discovery" }),
  ).toHaveLength(1);
  expect(within(techniques).getByText("From the detector and the AI")).toBeInTheDocument();
  expect(
    within(techniques).getByText("One internal host tried 150 ports on another."),
  ).toBeVisible();
});

test("opening a finding reads it once, though several parts of the page show it", async () => {
  const fake = signedInAs(OWNER, {
    [`GET ${FINDING}`]: { body: finding() },
    "GET /api/v1/attack-techniques/T1046": technique("T1046"),
  });

  renderAt(PAGE);

  await screen.findByRole("region", { name: "AI explanation" });
  await screen.findByRole("region", { name: "Activity" });
  const reads = fake.requests.filter(
    (request) => request.method === "GET" && request.url.endsWith(`/findings/${FINDING_ID}`),
  );
  expect(reads).toHaveLength(1);
});

test("a finding that isn't there, or isn't the organization's, says so", async () => {
  signedInAs(OWNER, { [`GET ${FINDING}`]: { status: 404, body: { title: "Not Found" } } });

  renderAt(PAGE);

  expect(await screen.findByRole("heading", { name: "Finding not found" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "All findings" })).toHaveAttribute(
    "href",
    `/app/orgs/${ORG_ID}/findings`,
  );
});
