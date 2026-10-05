/** How findings are named on screen (spec §8.2): severities, statuses, detectors, protocols. */
import type { components } from "../api/schema";

export type Severity = components["schemas"]["FindingSeverity"];
export type Status = components["schemas"]["FindingStatus"];

/** Most severe first, the order findings are triaged in. */
export const SEVERITIES: readonly Severity[] = ["critical", "high", "medium", "low"];

const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

const SEVERITY_LEVELS: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1 };

export const STATUSES: readonly Status[] = ["open", "investigating", "resolved", "false_positive"];

const STATUS_LABELS: Record<Status, string> = {
  open: "Open",
  investigating: "Investigating",
  resolved: "Resolved",
  false_positive: "False positive",
};

/** Milestone 1's detectors (spec §8.2), by the ID the API uses. */
export const DETECTORS: Record<string, string> = {
  port_scan: "Port scan",
  remote_access_bruteforce: "SSH/RDP brute force",
  outbound_volume: "Unusual outbound volume",
};

const PROTOCOLS: Record<number, string> = { 1: "ICMP", 6: "TCP", 17: "UDP" };

export function severityLabel(severity: Severity): string {
  return SEVERITY_LABELS[severity];
}

/** One to four bars, Low to Critical. */
export function severityLevel(severity: Severity): number {
  return SEVERITY_LEVELS[severity];
}

export function statusLabel(status: Status): string {
  return STATUS_LABELS[status];
}

export function detectorName(id: string): string {
  return DETECTORS[id] ?? id;
}

export function protocolName(protocol: number): string {
  return PROTOCOLS[protocol] ?? `Protocol ${protocol}`;
}

export function isSeverity(value: string | null): value is Severity {
  return value !== null && value in SEVERITY_LABELS;
}

export function isStatus(value: string | null): value is Status {
  return value !== null && value in STATUS_LABELS;
}
