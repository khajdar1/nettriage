/**
 * A finding's numbers (spec §8.2), as words. Each detector stores its own; a key this app doesn't
 * know yet is still shown, named from the key.
 */
import { formatNumber } from "../ui/format";

export interface MetricRow {
  label: string;
  value: string;
}

const LABELS: Record<string, string> = {
  variant: "Pattern",
  peak_distinct: "Peak in any 5 minutes",
  distinct_total: "Distinct in the window",
  flows: "Flows",
  rejected: "Rejected flows",
  scan_like_percent: "Rejected or tiny flows",
  source_internal: "Source inside the network",
  attempts: "Attempts",
  hosts: "Hosts tried",
  possible_success: "A login may have succeeded",
  megabytes: "Sent",
  dominant_port: "Main port",
  internal_hosts: "Internal hosts sending",
  robust_z_applied: "Compared with this network's usual volume",
};

const PATTERNS: Record<string, string> = {
  vertical: "Many ports on one host",
  horizontal: "One port on many hosts",
  single: "Many attempts on one host",
  spray: "A few attempts on many hosts",
};

/** Shown another way (bytes as megabytes), so left out. */
const HIDDEN = new Set(["bytes"]);

function label(key: string): string {
  const words = key.replaceAll("_", " ");
  return LABELS[key] ?? words.charAt(0).toUpperCase() + words.slice(1);
}

function value(key: string, raw: unknown): string | null {
  if (typeof raw === "boolean") {
    return raw ? "Yes" : "No";
  }
  if (typeof raw === "number") {
    if (key === "scan_like_percent") {
      return `${formatNumber(raw)}%`;
    }
    return key === "megabytes" ? `${formatNumber(raw)} MB` : formatNumber(raw);
  }
  if (typeof raw === "string") {
    return key === "variant" ? (PATTERNS[raw] ?? raw) : raw;
  }
  return null;
}

export function metricRows(metrics: Record<string, unknown>): MetricRow[] {
  const rows: MetricRow[] = [];
  for (const [key, raw] of Object.entries(metrics)) {
    const shown = HIDDEN.has(key) ? null : value(key, raw);
    if (shown !== null) {
      rows.push({ label: label(key), value: shown });
    }
  }
  return rows;
}
