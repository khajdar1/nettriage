import { expect, test } from "vitest";
import { SCAN_METRICS } from "../test/fixtures";
import { metricRows } from "./metrics";

test("a port scan's numbers read as words, with yes or no for what's true or false", () => {
  expect(metricRows(SCAN_METRICS)).toEqual([
    { label: "Pattern", value: "Many ports on one host" },
    { label: "Peak in any 5 minutes", value: "150" },
    { label: "Distinct in the window", value: "150" },
    { label: "Flows", value: "150" },
    { label: "Rejected flows", value: "150" },
    { label: "Rejected or tiny flows", value: "100%" },
    { label: "Source inside the network", value: "Yes" },
  ]);
});

test("outbound volume is shown in megabytes, not bytes", () => {
  expect(metricRows({ bytes: 524_288_000, megabytes: 500, dominant_port: 443 })).toEqual([
    { label: "Sent", value: "500 MB" },
    { label: "Main port", value: "443" },
  ]);
});

test("a number a newer detector adds is still shown, named from its key", () => {
  expect(metricRows({ beacon_interval: 60, nested: { a: 1 } })).toEqual([
    { label: "Beacon interval", value: "60" },
  ]);
});
