import { Link } from "react-router";
import { useOrg } from "../../orgs/org";
import { canContribute } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime, formatNumber } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { usePageTitle } from "../../ui/usePageTitle";
import { type Upload, useUploads } from "../../uploads/uploads";
import { UploadPanel } from "./UploadPanel";

const STATUS_LABELS: Record<Upload["status"], string> = {
  pending_upload: "Waiting for the file",
  processing: "Analyzing",
  analyzed: "Analyzed",
  failed: "Failed",
  expired: "Expired",
};

function UploadRow({ upload }: { upload: Upload }) {
  const rejected = upload.rows_rejected ?? 0;
  return (
    <tr>
      <td className="mono">{upload.original_filename}</td>
      <td>
        <span className="state">{STATUS_LABELS[upload.status]}</span>
        {upload.failure_reason !== null && <div className="muted">{upload.failure_reason}</div>}
        {upload.findings_truncated > 0 && (
          <div className="muted">
            {formatNumber(upload.findings_truncated)} lower-severity findings weren't kept
          </div>
        )}
      </td>
      <td>
        {upload.rows_parsed === null ? "—" : formatNumber(upload.rows_parsed)}
        {rejected > 0 && <div className="muted">{formatNumber(rejected)} rejected</div>}
      </td>
      <td className="date">{formatDateTime(upload.created_at)}</td>
      <td>
        {upload.status === "analyzed" && (
          <Link
            to={`../findings?upload=${upload.id}`}
            aria-label={`Findings from ${upload.original_filename}`}
          >
            Findings
          </Link>
        )}
      </td>
    </tr>
  );
}

/** `/app/orgs/:org/uploads`: the organization's flow logs, and sending a new one (spec §10). */
export function Uploads() {
  const org = useOrg();
  usePageTitle(`Uploads · ${org.name}`);
  const uploads = useUploads(org.id);
  const all = uploads.data?.pages.flatMap((page) => page.uploads) ?? [];
  return (
    <main id="main" className="page">
      <h1>Uploads</h1>
      {canContribute(org.role) && <UploadPanel org={org} />}
      {uploads.isPending && <Loading />}
      {uploads.isError && <ErrorNotice error={uploads.error} />}
      {uploads.isSuccess && all.length === 0 && <p className="muted">No uploads yet.</p>}
      {all.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Uploads to {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Status</th>
              <th scope="col">Rows</th>
              <th scope="col">Uploaded</th>
              <th scope="col">
                <span className="visually-hidden">Findings</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {all.map((upload) => (
              <UploadRow key={upload.id} upload={upload} />
            ))}
          </tbody>
        </table>
      )}
      {uploads.hasNextPage && (
        <button
          type="button"
          className="button more"
          disabled={uploads.isFetchingNextPage}
          onClick={() => void uploads.fetchNextPage()}
        >
          Show older uploads
        </button>
      )}
    </main>
  );
}
