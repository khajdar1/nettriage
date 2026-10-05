import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { unwrap } from "../../api/problem";
import { useMe } from "../../auth/session";
import {
  type FindingFilters as Filters,
  type FindingSummary,
  filtersFrom,
  searchFrom,
  useFindings,
} from "../../findings/findings";
import { statusLabel } from "../../findings/vocabulary";
import { type Member, memberName, useMembers } from "../../orgs/members";
import { useOrg } from "../../orgs/org";
import { type Overview, findingCount, unresolvedCount, useOverview } from "../../orgs/overview";
import { canContribute } from "../../orgs/permissions";
import { Ago } from "../../ui/Ago";
import { ErrorNotice } from "../../ui/ErrorNotice";
import { FindingSubline } from "../../ui/FindingSubline";
import { formatNumber } from "../../ui/format";
import { Loading } from "../../ui/Loading";
import { Person, Unassigned } from "../../ui/Person";
import { Severity } from "../../ui/Severity";
import { usePageTitle } from "../../ui/usePageTitle";
import { FindingFilters } from "./FindingFilters";
import { Glance } from "./Glance";

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

/** A "new in the last day" list says so, with the way back to any time; no select shows it. */
function NewScope({ filters }: { filters: Filters }) {
  return (
    <p className="scope">
      Detected in the last day.{" "}
      <Link to={{ search: searchFrom({ ...filters, new: undefined }).toString() }}>
        Show findings from any time
      </Link>
    </p>
  );
}

function FindingRow({
  finding,
  members,
  self,
}: {
  finding: FindingSummary;
  members?: Member[];
  self?: string;
}) {
  return (
    <tr>
      <td>
        <Severity severity={finding.severity} />
      </td>
      <td>
        <Link to={finding.id}>{finding.title}</Link>
        <FindingSubline finding={finding} />
      </td>
      <td>
        <span className="state">{statusLabel(finding.status)}</span>
      </td>
      <td>
        {finding.assignee_id === null ? (
          <Unassigned />
        ) : (
          <Person
            name={memberName(members, finding.assignee_id)}
            you={finding.assignee_id === self}
          />
        )}
      </td>
      <td>
        <Ago iso={finding.created_at} />
      </td>
    </tr>
  );
}

/** "14 unresolved of 45": the organization's, whatever the filters. */
function UnresolvedNote({ overview }: { overview: Overview }) {
  return (
    <span className="muted">
      {formatNumber(unresolvedCount(overview))} unresolved of {formatNumber(findingCount(overview))}
    </span>
  );
}

function NoFindings({
  filtered,
  closedOnly,
  contributor,
  onClear,
}: {
  filtered: boolean;
  closedOnly: boolean;
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
  if (closedOnly) {
    return (
      <div className="empty">
        <p>Nothing here is unresolved.</p>
        <p>
          Every finding is resolved or a false positive.{" "}
          <Link to={{ search: searchFrom({ status: "any", sort: "severity" }).toString() }}>
            Show every finding
          </Link>
        </p>
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
  const overview = useOverview(org.id);
  const members = useMembers(org.id);
  const me = useMe();
  const all = findings.data?.pages.flatMap((page) => page.findings) ?? [];
  const change = (next: Filters) => setSearch(searchFrom(next));
  // Unresolved and Any status aren't narrowing filters: with either alone, an empty list is empty.
  const filtered =
    [filters.severity, filters.assignee, filters.new, filters.detector, filters.upload].some(
      (value) => value !== undefined,
    ) ||
    (filters.status !== undefined && filters.status !== "any");
  const total = overview.data ? findingCount(overview.data) : null;
  return (
    <main id="main" className="page">
      <div className="page-head">
        <h1>Findings</h1>
        {overview.data && <UnresolvedNote overview={overview.data} />}
      </div>
      {overview.isError && <ErrorNotice error={overview.error} />}
      {overview.data && <Glance filters={filters} overview={overview.data} />}
      <FindingFilters filters={filters} onChange={change} />
      {filters.upload !== undefined && (
        <UploadScope orgId={org.id} filters={{ ...filters, upload: filters.upload }} />
      )}
      {filters.new !== undefined && <NewScope filters={filters} />}
      {findings.isPending && <Loading />}
      {findings.isError && <ErrorNotice error={findings.error} />}
      {findings.isSuccess && !overview.isPending && all.length === 0 && (
        <NoFindings
          filtered={filtered}
          closedOnly={filters.status === undefined && total !== 0}
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
              <th scope="col">Detected</th>
            </tr>
          </thead>
          <tbody>
            {all.map((finding) => (
              <FindingRow
                key={finding.id}
                finding={finding}
                members={members.data}
                self={me.data?.user.id}
              />
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
