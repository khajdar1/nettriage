import { Link } from "react-router";
import { type FindingFilters as Filters, searchFrom } from "../../findings/findings";
import {
  SEVERITIES,
  STATUSES,
  type Status,
  severityLabel,
  severityLevel,
  statusLabel,
} from "../../findings/vocabulary";
import { type Overview, findingCount } from "../../orgs/overview";
import { Ago } from "../../ui/Ago";
import { formatNumber } from "../../ui/format";
import { SeverityBars } from "../../ui/SeverityBars";
import { UPLOAD_STATUS_LABELS, type Upload } from "../../uploads/uploads";

/** Each number links to exactly the findings it counts: its view replaces the other filters. */
function view(filters: Filters, preset: Omit<Filters, "sort">): string {
  return `?${searchFrom({ ...preset, sort: filters.sort }).toString()}`;
}

function sameView(filters: Filters, preset: Omit<Filters, "sort">): boolean {
  return view(filters, preset) === view(filters, { ...filters });
}

const TRACK_CLASSES: Record<Status, string> = {
  open: "track-open",
  investigating: "track-investigating",
  resolved: "track-resolved",
  false_positive: "track-false-positive",
};

/** A share of the track's width, to three decimals: the CSP allows no inline style, so SVG
 * attributes carry the geometry, and percentages keep the hatching unstretched. */
function percent(value: number): string {
  return `${Number(value.toFixed(3))}%`;
}

/** Every finding by status, as one bar: ink for the unresolved, lighter for the closed. */
function StatusTrack({ overview }: { overview: Overview }) {
  const total = findingCount(overview);
  let x = 0;
  const parts = STATUSES.map((status) => {
    const width = total === 0 ? 0 : (overview.by_status[status] / total) * 100;
    const part = { status, x: percent(x), width: percent(width) };
    x += width;
    return part;
  }).filter((part) => part.width !== "0%");
  return (
    <svg className="status-track" aria-hidden="true" focusable="false">
      <defs>
        <pattern id="track-hatch" width="4" height="4" patternUnits="userSpaceOnUse">
          <rect className="hatch-ground" width="4" height="4" />
          <path className="hatch-line" d="M-1,1 l2,-2 M0,4 l4,-4 M3,5 l2,-2" />
        </pattern>
      </defs>
      {parts.map((part) => (
        <rect
          key={part.status}
          className={TRACK_CLASSES[part.status]}
          x={part.x}
          y="0"
          width={part.width}
          height="100%"
        />
      ))}
    </svg>
  );
}

function plural(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

function LastUpload({ filters, upload }: { filters: Filters; upload: Upload | null }) {
  if (upload === null) {
    return <p className="glance-upload muted">No uploads yet.</p>;
  }
  const outcome =
    upload.status === "analyzed"
      ? plural(upload.findings, "finding", "findings")
      : UPLOAD_STATUS_LABELS[upload.status].toLowerCase();
  return (
    <p className="glance-upload muted">
      Last upload{" "}
      <Link className="mono" to={view(filters, { status: "any", upload: upload.id })}>
        {upload.original_filename}
      </Link>
      , <Ago iso={upload.created_at} />: {outcome}.
    </p>
  );
}

/**
 * Above an organization's findings: the unresolved by severity, the quick views, every finding
 * by status and the last upload (Plan 6d). The numbers are the organization's, whatever the
 * filters, and each one links to the findings it counts.
 */
export function Glance({ filters, overview }: { filters: Filters; overview: Overview }) {
  const total = findingCount(overview);
  const quickViews = [
    { label: "Yours", count: overview.unresolved_mine, preset: { assignee: "me" } },
    { label: "Unassigned", count: overview.unresolved_unassigned, preset: { assignee: "none" } },
    {
      label: "New in the last day",
      count: overview.new_last_day,
      preset: { status: "any", new: "day" },
    },
  ] as const;
  return (
    <section className="glance" aria-label="Findings at a glance">
      <div>
        <h2 className="glance-title">Unresolved, by severity</h2>
        <div className="ladder">
          {SEVERITIES.map((severity) => {
            const count = overview.unresolved_by_severity[severity];
            return (
              <Link
                key={severity}
                to={view(filters, { severity })}
                className={count === 0 ? "rung zero" : "rung"}
                aria-label={`${count} ${severityLabel(severity)} findings, unresolved`}
              >
                <SeverityBars level={severityLevel(severity)} />
                <b>{formatNumber(count)}</b>
                <span>{severityLabel(severity)}</span>
              </Link>
            );
          })}
        </div>
        <div className="views">
          {quickViews.map(({ label, count, preset }) => (
            <Link
              key={label}
              to={view(filters, preset)}
              className="chip"
              aria-current={sameView(filters, preset) ? "true" : undefined}
            >
              {label} <b>{formatNumber(count)}</b>
            </Link>
          ))}
        </div>
      </div>
      <div>
        <h2 className="glance-title">All {formatNumber(total)}, by status</h2>
        <StatusTrack overview={overview} />
        <div className="legend">
          {STATUSES.map((status) => (
            <Link
              key={status}
              to={view(filters, { status })}
              aria-label={`${statusLabel(status)}: ${overview.by_status[status]} findings`}
            >
              <span className="legend-key">
                <span className={`key ${TRACK_CLASSES[status]}`} aria-hidden="true" />
                {statusLabel(status)}
              </span>
              <b>{formatNumber(overview.by_status[status])}</b>
            </Link>
          ))}
        </div>
        <LastUpload filters={filters} upload={overview.last_upload} />
      </div>
    </section>
  );
}
