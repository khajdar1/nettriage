import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { errorMessage } from "../../api/problem";
import type { Org } from "../../orgs/org";
import { type Progress, UploadProblem, sendUpload } from "../../uploads/sendUpload";
import { uploadsKey } from "../../uploads/uploads";

function problemText(error: unknown): string {
  return error instanceof UploadProblem ? error.message : errorMessage(error);
}

/**
 * Sending a flow log (spec §10): choose a file, then upload it, with its progress shown in place.
 * It sits on the page rather than in a dialog: Plan 6a's pages have no modal dialogs.
 */
export function UploadPanel({ org }: { org: Org }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const send = useMutation({
    mutationFn: (chosen: File) => sendUpload(org.id, chosen, setProgress),
    onSettled: () => setProgress(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: uploadsKey(org.id) }),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (file !== null) {
      send.mutate(file);
    }
  }
  return (
    <section className="setting" aria-labelledby="upload-title">
      <div className="setting-what">
        <h2 id="upload-title">Upload a flow log</h2>
        <p>
          VPC Flow Logs in the default format, plain or gzipped, up to 25 MB. Analysis takes about a
          minute.
        </p>
      </div>
      <div className="setting-how">
        <form className="inline-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="upload-file">Flow log file</label>
            <input
              id="upload-file"
              type="file"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                send.reset();
              }}
            />
          </div>
          <button
            type="submit"
            className="button button-primary"
            disabled={file === null || send.isPending}
          >
            Upload
          </button>
        </form>
        <div role="status" className="upload-status">
          {progress?.phase === "reading" && <p className="muted">Reading the file…</p>}
          {progress?.phase === "sending" && (
            <p className="muted">
              <progress max={100} value={progress.percent} aria-label="Upload progress" /> Sending,{" "}
              {progress.percent}%
            </p>
          )}
          {send.isSuccess && (
            <p>
              Uploaded {send.data.original_filename}. It's being analyzed, and shows as Analyzed in
              about a minute.
            </p>
          )}
        </div>
        {send.isError && (
          <p role="alert" className="notice notice-error">
            {problemText(send.error)}
          </p>
        )}
      </div>
    </section>
  );
}
