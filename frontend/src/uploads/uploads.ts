/** An organization's uploads (spec §7), newest first, a page at a time. */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Upload = components["schemas"]["UploadOut"];

/** A file the API signed for but never received is watched only this long. */
const WAIT_FOR_FILE_MS = 10 * 60_000;
/** An analysis still running after this long has stopped; the daily check fails it (Plan 7). */
const ANALYSIS_MS = 2 * 60 * 60_000;
const POLL_MS = 3000;

/** How an upload's state reads on screen. */
export const UPLOAD_STATUS_LABELS: Record<Upload["status"], string> = {
  pending_upload: "Waiting for the file",
  processing: "Analyzing",
  analyzed: "Analyzed",
  failed: "Failed",
  expired: "Expired",
};

export function uploadsKey(orgId: string) {
  return ["uploads", orgId] as const;
}

/**
 * Still changing: being analyzed (for at most two hours), or waiting a few minutes for its file.
 * Each check reads the whole list, so a stuck upload mustn't be watched for ever.
 */
export function isBusy(upload: Upload, now: number): boolean {
  const age = now - Date.parse(upload.created_at);
  if (upload.status === "processing") {
    return age < ANALYSIS_MS;
  }
  return upload.status === "pending_upload" && age < WAIT_FOR_FILE_MS;
}

/** One upload, read on its own: a list scoped to it, or a finding's summary, names its file. */
export function useUpload(orgId: string, uploadId: string) {
  return useQuery({
    queryKey: ["upload", orgId, uploadId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/uploads/{upload_id}", {
          params: { path: { org_id: orgId, upload_id: uploadId } },
        }),
      ),
  });
}

/** The uploads, rechecked every few seconds while one of them is still changing. */
export function useUploads(orgId: string) {
  return useInfiniteQuery({
    queryKey: uploadsKey(orgId),
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/uploads", {
          params: { path: { org_id: orgId }, query: pageParam ? { cursor: pageParam } : {} },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (query) => {
      const now = Date.now();
      const pages = query.state.data?.pages ?? [];
      return pages.some((page) => page.uploads.some((upload) => isBusy(upload, now)))
        ? POLL_MS
        : false;
    },
  });
}
