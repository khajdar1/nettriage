/** Data the fake API answers with, typed by the API's own schema. */
import type { Me, Membership } from "../auth/session";
import type { Org } from "../orgs/org";
import type { Member } from "../orgs/members";
import type { Finding } from "../findings/finding";
import type { FindingSummary } from "../findings/findings";
import type { Upload } from "../uploads/uploads";
import type { Reply } from "./fakeApi";

export const SIGNED_OUT: Reply = {
  status: 401,
  body: { type: "about:blank", title: "Unauthorized", status: 401, detail: null, trace_id: null },
};

export const ME: Me = {
  user: {
    id: "01a0e9e9-75d3-7462-9e22-4f476c3e802c",
    email: "ana@example.com",
    display_name: null,
  },
  memberships: [],
  csrf_token: "csrf-1",
};

export const ORG_ID = "01a10333-a115-741b-91ff-41d6e310d817";

export const ACME: Membership = {
  org_id: ORG_ID,
  name: "Acme Security",
  slug: "acme-security",
  role: "owner",
};

export function memberOf(...memberships: Membership[]): Me {
  return { ...ME, memberships };
}

export function org(role: Org["role"] = "owner"): Org {
  return {
    id: ORG_ID,
    name: "Acme Security",
    slug: "acme-security",
    role,
    member_count: 3,
    created_at: "2026-10-01T09:00:00Z",
  };
}

export const OWNER: Member = {
  user_id: ME.user.id,
  email: "ana@example.com",
  display_name: null,
  role: "owner",
  joined_at: "2026-10-01T09:00:00Z",
};

export const ADMIN: Member = {
  user_id: "01a0e9e9-0000-7000-8000-00000000000a",
  email: "ben@example.com",
  display_name: "Ben Admin",
  role: "admin",
  joined_at: "2026-10-02T09:00:00Z",
};

export const VIEWER: Member = {
  user_id: "01a0e9e9-0000-7000-8000-00000000000b",
  email: "cleo@example.com",
  display_name: null,
  role: "viewer",
  joined_at: "2026-10-02T10:00:00Z",
};

export const UPLOAD_ID = "01a10500-0000-7000-8000-000000000001";

export function upload(fields: Partial<Upload> = {}): Upload {
  return {
    id: UPLOAD_ID,
    original_filename: "port-scan.log",
    size_bytes: 13_312,
    sha256: "a".repeat(64),
    status: "analyzed",
    failure_reason: null,
    rows_parsed: 1204,
    rows_rejected: 3,
    rejected_samples: [],
    findings_truncated: 0,
    flow_start: "2026-10-01T12:00:00Z",
    flow_end: "2026-10-01T12:05:00Z",
    uploaded_by: ME.user.id,
    created_at: "2026-10-04T09:30:00Z",
    processed_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
}

export const FINDING_ID = "01a10600-0000-7000-8000-000000000001";

export function findingSummary(fields: Partial<FindingSummary> = {}): FindingSummary {
  return {
    id: FINDING_ID,
    upload_id: UPLOAD_ID,
    detector_id: "port_scan",
    detector_version: 1,
    severity: "high",
    status: "open",
    title: "Port scan of 10.0.0.5 from 10.0.3.17: 150 TCP ports in 5 minutes",
    src_ip: "10.0.3.17",
    dst_ip: "10.0.0.5",
    dst_port: null,
    protocol: 6,
    window_start: "2026-10-01T12:00:00Z",
    window_end: "2026-10-01T12:05:00Z",
    assignee_id: null,
    version: 1,
    created_at: "2026-10-04T09:31:00Z",
    ...fields,
  };
}

export const SCAN_METRICS = {
  variant: "vertical",
  peak_distinct: 150,
  distinct_total: 150,
  flows: 150,
  rejected: 150,
  scan_like_percent: 100,
  source_internal: true,
};

export function finding(fields: Partial<Finding> = {}): Finding {
  return {
    ...findingSummary(),
    metrics: SCAN_METRICS,
    evidence: [22, 80, 443].map((port, index) => ({
      src_ip: "10.0.3.17",
      dst_ip: "10.0.0.5",
      src_port: 40000 + index,
      dst_port: port,
      protocol: 6,
      packets: 1,
      bytes: 40,
      start: `2026-10-01T12:00:0${index}Z`,
      end: `2026-10-01T12:00:0${index}Z`,
      action: "REJECT",
      line_no: index + 1,
    })),
    techniques: [
      {
        id: "T1046",
        name: "Network Service Discovery",
        url: "https://attack.mitre.org/techniques/T1046/",
        source: "detector",
        rationale: null,
      },
    ],
    events: [
      {
        id: "01a10700-0000-7000-8000-000000000001",
        type: "created",
        actor_id: null,
        payload: {},
        created_at: "2026-10-04T09:31:00Z",
      },
    ],
    events_total: 1,
    ai_analysis: null,
    ...fields,
  };
}
