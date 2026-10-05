import type { Finding } from "../../../findings/finding";
import { formatNumber } from "../../../ui/format";
import { PortMap } from "../../../ui/PortMap";

/**
 * A port scan of one host, drawn on the port map (the owner's decision, Plan 6b). It shows the
 * ports in the stored evidence, which keeps at most 50 flows, and says so.
 */
export function EvidenceMap({ finding }: { finding: Finding }) {
  const ports = finding.evidence.map((flow) => flow.dst_port);
  const oneHost = finding.detector_id === "port_scan" && finding.dst_ip !== null;
  if (!oneHost || finding.dst_port !== null || !ports.some((port) => port < 1024)) {
    return null;
  }
  const { distinct_total: total, flows } = finding.metrics;
  const probed = typeof total === "number" ? total : new Set(ports).size;
  const sampled = typeof flows === "number" && finding.evidence.length < flows;
  const caption = `Ports 0 to 1023 of ${finding.dst_ip}. The scan probed ${formatNumber(probed)} ports${
    sampled ? "; the evidence keeps a sample." : "."
  }`;
  return (
    <PortMap ports={ports} caption={caption} tallyLabel={sampled ? "in the sample" : "probed"} />
  );
}
