/**
 * Sending a flow log (spec §4.2, §7): the browser fingerprints the file, asks the API for a slot,
 * then puts the file straight into S3 with the URL and headers the API signed. S3 accepts only a
 * file of that exact length and SHA-256.
 */
import { api, idempotencyKey, sha256Hex } from "../api/client";
import { unwrap } from "../api/problem";
import type { Upload } from "./uploads";

/** 25 MB, as the API and S3 enforce (spec §5.7). */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

/** A problem with the file or with storage, said in words the person can act on. */
export class UploadProblem extends Error {
  override name = "UploadProblem";
}

export type Progress = { phase: "reading" } | { phase: "sending"; percent: number };

/** Puts the file with an `XMLHttpRequest`, the one browser API that reports upload progress. */
function putFile(
  url: string,
  headers: Record<string, string>,
  body: ArrayBuffer,
  onProgress: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    for (const [name, value] of Object.entries(headers)) {
      request.setRequestHeader(name, value);
    }
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((100 * event.loaded) / event.total));
      }
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        resolve();
      } else {
        reject(
          new UploadProblem(
            `Storage refused the file (status ${request.status}). Upload it again.`,
          ),
        );
      }
    };
    request.onerror = () => {
      reject(
        new UploadProblem(
          "The file didn't reach storage. Check your connection and upload it again.",
        ),
      );
    };
    request.send(body);
  });
}

/**
 * Uploads `file` to the organization and returns its upload, now waiting for analysis. Each call
 * is a new upload with its own idempotency key: a signed URL lasts only 5 minutes, so a replayed
 * answer could no longer be used.
 */
export async function sendUpload(
  orgId: string,
  file: File,
  onProgress: (progress: Progress) => void,
): Promise<Upload> {
  if (file.size === 0) {
    throw new UploadProblem("This file is empty.");
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new UploadProblem("Files can be at most 25 MB.");
  }
  onProgress({ phase: "reading" });
  const bytes = await file.arrayBuffer();
  const created = unwrap(
    await api.POST("/api/v1/orgs/{org_id}/uploads", {
      params: { path: { org_id: orgId } },
      body: { filename: file.name, size_bytes: file.size, sha256: await sha256Hex(bytes) },
      headers: { "Idempotency-Key": idempotencyKey() },
    }),
  );
  onProgress({ phase: "sending", percent: 0 });
  await putFile(created.upload_url, created.upload_headers, bytes, (percent) =>
    onProgress({ phase: "sending", percent }),
  );
  return created.upload;
}
