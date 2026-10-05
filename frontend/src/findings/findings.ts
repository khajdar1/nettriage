/** An organization's findings (spec §7, §10): filtered and sorted by the API, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";
import { DETECTORS, type Severity, type Status, isSeverity, isStatus } from "./vocabulary";

export type FindingSummary = components["schemas"]["FindingSummaryOut"];
export type Sort = "severity" | "newest";

export interface FindingFilters {
  severity?: Severity;
  status?: Status;
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
  const detector = search.get("detector");
  const upload = search.get("upload");
  if (isSeverity(severity)) {
    filters.severity = severity;
  }
  if (isStatus(status)) {
    filters.status = status;
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
  for (const name of ["severity", "status", "detector", "upload"] as const) {
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

export function useFindings(orgId: string, filters: FindingFilters) {
  // The API takes any of several statuses (Plan 6d).
  const { status, ...rest } = filters;
  return useInfiniteQuery({
    queryKey: findingsKey(orgId, filters),
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/findings", {
          params: {
            path: { org_id: orgId },
            query: {
              ...rest,
              ...(status ? { status: [status] } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}
