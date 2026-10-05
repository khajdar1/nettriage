import type { FindingFilters as Filters } from "../../findings/findings";
import {
  DETECTORS,
  SEVERITIES,
  STATUSES,
  isSeverity,
  isStatus,
  severityLabel,
  statusLabel,
} from "../../findings/vocabulary";

/**
 * The findings list's filters and order (spec §10). Each choice applies at once: it narrows the
 * list on the page and changes nothing else.
 */
export function FindingFilters({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (filters: Filters) => void;
}) {
  function set(name: "severity" | "status" | "assignee" | "detector", value: string) {
    const next: Filters = { ...filters };
    if (name === "severity") {
      next.severity = isSeverity(value) ? value : undefined;
    } else if (name === "status") {
      next.status = value === "any" || isStatus(value) ? value : undefined;
    } else if (name === "assignee") {
      next.assignee = value === "me" || value === "none" ? value : undefined;
    } else {
      next.detector = value === "" ? undefined : value;
    }
    onChange(next);
  }
  return (
    <div className="filters" role="group" aria-label="Filter and sort findings">
      <div className="field">
        <label htmlFor="filter-severity">Severity</label>
        <select
          id="filter-severity"
          value={filters.severity ?? ""}
          onChange={(event) => set("severity", event.target.value)}
        >
          <option value="">Any severity</option>
          {SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {severityLabel(severity)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-status">Status</label>
        <select
          id="filter-status"
          value={filters.status ?? ""}
          onChange={(event) => set("status", event.target.value)}
        >
          <option value="">Unresolved</option>
          <option value="any">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {statusLabel(status)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-assignee">Assignee</label>
        <select
          id="filter-assignee"
          value={filters.assignee ?? ""}
          onChange={(event) => set("assignee", event.target.value)}
        >
          <option value="">Anyone</option>
          <option value="me">Yours</option>
          <option value="none">Unassigned</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-detector">Detector</label>
        <select
          id="filter-detector"
          value={filters.detector ?? ""}
          onChange={(event) => set("detector", event.target.value)}
        >
          <option value="">Any detector</option>
          {Object.entries(DETECTORS).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="filter-sort">Sort</label>
        <select
          id="filter-sort"
          value={filters.sort}
          onChange={(event) =>
            onChange({ ...filters, sort: event.target.value === "newest" ? "newest" : "severity" })
          }
        >
          <option value="severity">Most severe first</option>
          <option value="newest">Newest first</option>
        </select>
      </div>
    </div>
  );
}
