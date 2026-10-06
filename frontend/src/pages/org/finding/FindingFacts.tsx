import { Fragment } from "react";
import { Link } from "react-router";
import type { Finding } from "../../../findings/finding";
import { metricRows } from "../../../findings/metrics";
import { detectorName, protocolName } from "../../../findings/vocabulary";
import { formatDateTime, formatTime } from "../../../ui/format";

/** What the detector saw (spec §10): who, where, when, and the detector's own numbers. */
export function FindingFacts({ finding }: { finding: Finding }) {
  return (
    <section className="facts" aria-labelledby="facts-title">
      <h2 id="facts-title">Details</h2>
      <dl>
        <dt>Source</dt>
        <dd className="mono">{finding.src_ip}</dd>
        <dt>Destination</dt>
        <dd className={finding.dst_ip === null ? undefined : "mono"}>
          {finding.dst_ip ?? "Several"}
        </dd>
        <dt>Port</dt>
        <dd>{finding.dst_port ?? "Several"}</dd>
        <dt>Protocol</dt>
        <dd>{finding.protocol === null ? "Several" : protocolName(finding.protocol)}</dd>
        <dt>Window</dt>
        <dd>
          {formatDateTime(finding.window_start)} to {formatTime(finding.window_end)}
        </dd>
        <dt>Detector</dt>
        <dd>
          {detectorName(finding.detector_id)}, version {finding.detector_version}
        </dd>
        {metricRows(finding.metrics).map((row) => (
          <Fragment key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </Fragment>
        ))}
        <dt>Upload</dt>
        <dd>
          <Link to={`../findings?status=any&upload=${finding.upload_id}`}>
            Other findings from this upload
          </Link>
        </dd>
      </dl>
    </section>
  );
}
