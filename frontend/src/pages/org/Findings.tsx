import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import {
  type FindingFilters as Filters,
  type FindingSummary,
  filtersFrom,
  searchFrom,
  useFindings,
} from "../../findings/findings";
import { detectorName, statusLabel } from "../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { canContribute } from "../../orgs/permissions";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { formatDateTime } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { Severity } from "../../ui/Severity";
import { usePageTitle } from "../../ui/usePageTitle";
import { FindingFilters } from "./FindingFilters";

/** Which upload the list is narrowed to, by its file name, and the way back to all of them. */
function UploadScope({ orgId, filters }: { orgId: string; filters: Filters & { upload: string } }) {
  const upload = useQuery({
    queryKey: ["upload", orgId, filters.upload],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/orgs/{org_id}/uploads/{upload_id}", {
          params: { path: { org_id: orgId, upload_id: filters.upload } },
        }),
      ),
  });
  const everyUpload = searchFrom({ ...filters, upload: undefined }).toString();
  return (
    <p className="scope">
      Findings from <span className="mono">{upload.data?.original_filename ?? "one upload"}</span>.{" "}
      <Link to={{ search: everyUpload }}>Show every upload's findings</Link>
    </p>
  );
}

function FindingRow({ finding, members }: { finding: FindingSummary; members?: Member[] }) {
  return (
    <tr>
      <td>
        <Severity severity={finding.severity} />
      </td>
      <td>
        <Link to={finding.id}>{finding.title}</Link>
        <div className="muted">{detectorName(finding.detector_id)}</div>
      </td>
      <td>
        <span className="state">{statusLabel(finding.status)}</span>
      </td>
      <td className="nowrap">
        {finding.assignee_id === null ? (
          <span className="muted">Unassigned</span>
        ) : (
          memberName(members, finding.assignee_id)
        )}
      </td>
      <td className="date">{formatDateTime(finding.window_start)}</td>
    </tr>
  );
}

function NoFindings({
  filtered,
  contributor,
  onClear,
}: {
  filtered: boolean;
  contributor: boolean;
  onClear: () => void;
}) {
  if (filtered) {
    return (
      <div className="empty">
        <p>No findings match these filters.</p>
        <button type="button" className="button" onClick={onClear}>
          Clear filters
        </button>
      </div>
    );
  }
  return (
    <div className="empty">
      <p>No findings yet.</p>
      {contributor ? (
        <p>
          <Link to="../uploads">Upload a flow log</Link> and NetTriage looks for scans, brute force
          and unusual outbound volume in it.
        </p>
      ) : (
        <p className="muted">Findings appear here once a flow log has been analyzed.</p>
      )}
    </div>
  );
}

/** `/app/orgs/:org/findings`, where an organization opens: its findings, worst first (spec §10). */
export function Findings() {
  const org = useOrg();
  usePageTitle(`Findings · ${org.name}`);
  const [search, setSearch] = useSearchParams();
  const filters = filtersFrom(search);
  const findings = useFindings(org.id, filters);
  const members = useMembers(org.id);
  const all = findings.data?.pages.flatMap((page) => page.findings) ?? [];
  const change = (next: Filters) => setSearch(searchFrom(next));
  const filtered = [filters.severity, filters.status, filters.detector, filters.upload].some(
    (value) => value !== undefined,
  );
  return (
    <main id="main" className="page">
      <h1>Findings</h1>
      <FindingFilters filters={filters} onChange={change} />
      {filters.upload !== undefined && (
        <UploadScope orgId={org.id} filters={{ ...filters, upload: filters.upload }} />
      )}
      {findings.isPending && <Loading />}
      {findings.isError && <ErrorNotice error={findings.error} />}
      {findings.isSuccess && all.length === 0 && (
        <NoFindings
          filtered={filtered}
          contributor={canContribute(org.role)}
          onClear={() => change({ sort: filters.sort })}
        />
      )}
      {all.length > 0 && (
        <table className="table">
          <caption className="visually-hidden">Findings in {org.name}</caption>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Finding</th>
              <th scope="col">Status</th>
              <th scope="col">Assignee</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {all.map((finding) => (
              <FindingRow key={finding.id} finding={finding} members={members.data} />
            ))}
          </tbody>
        </table>
      )}
      {findings.hasNextPage && (
        <button
          type="button"
          className="button more"
          disabled={findings.isFetchingNextPage}
          onClick={() => void findings.fetchNextPage()}
        >
          Show more findings
        </button>
      )}
    </main>
  );
}
