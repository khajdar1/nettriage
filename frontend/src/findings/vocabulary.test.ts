import { expect, test } from "vitest";
import {
  DETECTORS,
  SEVERITIES,
  STATUSES,
  detectorName,
  isSeverity,
  isStatus,
  protocolName,
  severityLabel,
  severityLevel,
  statusLabel,
} from "./vocabulary";

test("severities run most severe first, each with a word and one to four bars", () => {
  expect(SEVERITIES.map(severityLabel)).toEqual(["Critical", "High", "Medium", "Low"]);
  expect(SEVERITIES.map(severityLevel)).toEqual([4, 3, 2, 1]);
});

test("statuses read as words", () => {
  expect(STATUSES.map(statusLabel)).toEqual([
    "Open",
    "Investigating",
    "Resolved",
    "False positive",
  ]);
});

test("detectors have names, and an unknown one shows its ID", () => {
  expect(Object.keys(DETECTORS)).toEqual([
    "port_scan",
    "remote_access_bruteforce",
    "outbound_volume",
  ]);
  expect(detectorName("remote_access_bruteforce")).toBe("SSH/RDP brute force");
  expect(detectorName("dns_tunnel")).toBe("dns_tunnel");
});

test("protocols are named by their number", () => {
  expect([6, 17, 1, 47].map(protocolName)).toEqual(["TCP", "UDP", "ICMP", "Protocol 47"]);
});

test("only real severities and statuses are taken from an address", () => {
  expect(["high", "urgent", null].map(isSeverity)).toEqual([true, false, false]);
  expect(["false_positive", "closed", null].map(isStatus)).toEqual([true, false, false]);
});
