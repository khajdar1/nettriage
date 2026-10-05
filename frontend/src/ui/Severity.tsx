import { type Severity as Level, severityLabel, severityLevel } from "../findings/vocabulary";
import { SeverityBars } from "./SeverityBars";

/** A finding's severity: its word, with its bars beside it (amber: a detector's verdict). */
export function Severity({ severity }: { severity: Level }) {
  return (
    <span className="severity">
      <SeverityBars level={severityLevel(severity)} />
      {severityLabel(severity)}
    </span>
  );
}
