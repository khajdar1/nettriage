/** An organization's findings (spec §7, §10): filtered and sorted by the API, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components, operations } from "../api/schema";
import { DETECTORS, type Severity, type Status, isSeverity, isStatus } from "./vocabulary";

export type FindingSummary = components["schemas"]["FindingSummaryOut"];
export type Sort = "severity" | "newest";
export type Assignee = "me" | "none";
type Query = NonNullable<
  operations["findings_api_v1_orgs__org_id__findings_get"]["parameters"]["query"]
>;

/** Still to be dealt with (Plan 6d): what the list shows unless the person picks a status. */
export const UNRESOLVED: readonly Status[] = ["open", "investigating"];

const DAY_MS = 86_400_000;

export interface FindingFilters {
  severity?: Severity;
  /** Absent: Unresolved, the default (Plan 6d). `any`: every status. */
  status?: Status | "any";
  /** `me`: assigned to the signed-in person; `none`: to nobody. */
  assignee?: Assignee;
  /** `day`: detected in the last 24 hours. */
  new?: "day";
  detector?: string;
  upload?: string;
  /** Most severe first unless the person picks newest (the owner's decision, Plan 6b). */
  sort: Sort;
}

export function findingsKey(orgId: string, filters?: FindingFilters) {
  return filters === undefined
    ? (["findings", orgId] as const)
    : (["findings", orgId, filters] as const);
}

/** The filters in an address such as `?severity=high&sort=newest`; anything unknown is no filter. */
export function filtersFrom(search: URLSearchParams): FindingFilters {
  const filters: FindingFilters = { sort: search.get("sort") === "newest" ? "newest" : "severity" };
  const severity = search.get("severity");
  const status = search.get("status");
  const assignee = search.get("assignee");
  const detector = search.get("detector");
  const upload = search.get("upload");
  if (isSeverity(severity)) {
    filters.severity = severity;
  }
  if (status === "any" || isStatus(status)) {
    filters.status = status;
  }
  if (assignee === "me" || assignee === "none") {
    filters.assignee = assignee;
  }
  if (search.get("new") === "day") {
    filters.new = "day";
  }
  if (detector !== null && detector in DETECTORS) {
    filters.detector = detector;
  }
  if (upload !== null && upload !== "") {
    filters.upload = upload;
  }
  return filters;
}

/** The address for these filters, keeping only what differs from the defaults. */
export function searchFrom(filters: FindingFilters): URLSearchParams {
  const search = new URLSearchParams();
  for (const name of ["severity", "status", "assignee", "new", "detector", "upload"] as const) {
    const value = filters[name];
    if (value !== undefined) {
      search.set(name, value);
    }
  }
  if (filters.sort === "newest") {
    search.set("sort", "newest");
  }
  return search;
}

/** What the API is asked for: Unresolved is two statuses, and "new" a moment a day before now. */
export function apiQuery(filters: FindingFilters, now: number): Query {
  const { status, new: recent, ...rest } = filters;
  const query: Query = { ...rest };
  if (status === undefined) {
    query.status = [...UNRESOLVED];
  } else if (status !== "any") {
    query.status = [status];
  }
  if (recent === "day") {
    query.since = new Date(now - DAY_MS).toISOString();
  }
  return query;
}

export function useFindings(orgId: string, filters: FindingFilters) {
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/findings", {
          params: {
            path: { org_id: orgId },
            query: {
              ...apiQuery(filters, Date.now()),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
