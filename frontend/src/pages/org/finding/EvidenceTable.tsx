import type { Finding } from "../../../findings/finding";
import { protocolName } from "../../../findings/vocabulary";
import { formatClock, formatNumber } from "../../../ui/format";

/** An address and port as written in logs; IPv6 addresses go in brackets. */
function endpoint(ip: string, port: number): string {
  return ip.includes(":") ? `[${ip}]:${port}` : `${ip}:${port}`;
}

/** The flows the detector kept as evidence (spec §10), at most 50, in time order. */
export function EvidenceTable({ finding }: { finding: Finding }) {
  const { flows } = finding.metrics;
  const sampledFrom =
    typeof flows === "number" && flows > finding.evidence.length
      ? ` sampled from ${formatNumber(flows)}`
      : "";
  return (
    // Wider than its column on most screens: it scrolls on its own, by keyboard too.
    <div className="table-scroll" role="region" aria-label="Evidence flows" tabIndex={0}>
      <table className="table evidence">
        <caption>
          Evidence: {finding.evidence.length} flows{sampledFrom}, in time order
        </caption>
        <thead>
          <tr>
            <th scope="col">Line</th>
            <th scope="col">Time</th>
            <th scope="col">Source</th>
            <th scope="col">Destination</th>
            <th scope="col">Action</th>
            <th scope="col">Protocol</th>
            <th scope="col">Packets</th>
            <th scope="col">Bytes</th>
          </tr>
        </thead>
        <tbody>
          {finding.evidence.map((flow) => (
            <tr key={flow.line_no}>
              <td className="mono">{flow.line_no}</td>
              <td className="date">{formatClock(flow.start)}</td>
              <td className="mono">{endpoint(flow.src_ip, flow.src_port)}</td>
              <td className="mono">{endpoint(flow.dst_ip, flow.dst_port)}</td>
              <td className="mono">{flow.action}</td>
              <td>{protocolName(flow.protocol)}</td>
              <td>{formatNumber(flow.packets)}</td>
              <td>{formatNumber(flow.bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
