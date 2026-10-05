/** An organization's uploads (spec §7), newest first, a page at a time. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { unwrap } from "../api/problem";
import type { components } from "../api/schema";

export type Upload = components["schemas"]["UploadOut"];

/** A file the API signed for but never received is watched only this long. */
const WAIT_FOR_FILE_MS = 10 * 60_000;
const POLL_MS = 3000;

export function uploadsKey(orgId: string) {
  return ["uploads", orgId] as const;
}

/** Still changing: being analyzed, or waiting a few minutes for its file. */
export function isBusy(upload: Upload, now: number): boolean {
  if (upload.status === "processing") {
    return true;
  }
  return (
    upload.status === "pending_upload" && now - Date.parse(upload.created_at) < WAIT_FOR_FILE_MS
  );
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
