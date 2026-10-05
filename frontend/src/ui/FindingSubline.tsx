import type { FindingSummary } from "../findings/findings";
import { detectorName } from "../findings/vocabulary";
import { AiMark } from "./AiMark";

/** An address and an optional port, IPv6 in brackets. */
function endpoint(ip: string, port: number | null): string {
  const host = ip.includes(":") ? `[${ip}]` : ip;
  return port === null ? host : `${host}:${port}`;
}

/** Where the traffic went: source → destination, with "many hosts" for one port on many. */
export function flowOf(finding: FindingSummary): string {
  const destination =
    finding.dst_ip === null
      ? `many hosts${finding.dst_port === null ? "" : `:${finding.dst_port}`}`
      : endpoint(finding.dst_ip, finding.dst_port);
  return `${endpoint(finding.src_ip, null)} → ${destination}`;
}

/** Under a finding's title in a list: its detector, its flow and whether the AI explained it. */
export function FindingSubline({ finding }: { finding: FindingSummary }) {
  return (
    <div className="subline">
      <span>{detectorName(finding.detector_id)}</span>
      <span className="flow">{flowOf(finding)}</span>
      <AiMark status={finding.ai_status} />
    </div>
  );
}
