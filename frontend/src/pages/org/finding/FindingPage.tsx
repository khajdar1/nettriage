import { Link, useParams } from "react-router";
import { ApiError } from "../../../api/problem";
import { useFinding } from "../../../findings/finding";
import { detectorName } from "../../../findings/vocabulary";
import { useOrg } from "../../../orgs/org";
import { ErrorNotice } from "../../../ui/ErrorNotice";
import { Loading } from "../../../ui/Loading";
import { Severity } from "../../../ui/Severity";
import { usePageTitle } from "../../../ui/usePageTitle";
import { EvidenceMap } from "./EvidenceMap";
import { EvidenceTable } from "./EvidenceTable";
import { FindingFacts } from "./FindingFacts";
import { Techniques } from "./Techniques";

function FindingNotFound() {
  return (
    <main id="main" className="page narrow">
      <h1>Finding not found</h1>
      <p>The link may be wrong, or the finding may belong to another organization.</p>
      <Link to="../findings">All findings</Link>
    </main>
  );
}

/** `/app/orgs/:org/findings/:id`: one finding, its evidence and what's known about it (spec §10). */
export function FindingPage() {
  const org = useOrg();
  const { findingId = "" } = useParams();
  const finding = useFinding(org.id, findingId);
  const subject =
    finding.data === undefined
      ? "Finding"
      : `${detectorName(finding.data.detector_id)} from ${finding.data.src_ip}`;
  usePageTitle(`${subject} · ${org.name}`);
  if (finding.isPending) {
    return (
      <main id="main" className="page">
        <Loading />
      </main>
    );
  }
  if (finding.isError) {
    if (finding.error instanceof ApiError && finding.error.status === 404) {
      return <FindingNotFound />;
    }
    return (
      <main id="main" className="page">
        <ErrorNotice error={finding.error} />
      </main>
    );
  }
  const found = finding.data;
  return (
    <main id="main" className="page">
      <p className="back">
        <Link to="../findings">All findings</Link>
      </p>
      <p className="finding-kind">
        <Severity severity={found.severity} />
        <span>{detectorName(found.detector_id)}</span>
      </p>
      <h1 className="finding-title">{found.title}</h1>
      <div className="finding-grid">
        <div className="finding-main">
          <section aria-labelledby="evidence-title">
            <h2 id="evidence-title">Evidence</h2>
            <EvidenceMap finding={found} />
            <EvidenceTable finding={found} />
          </section>
        </div>
        <div className="finding-side">
          <FindingFacts finding={found} />
          <Techniques techniques={found.techniques} />
        </div>
      </div>
    </main>
  );
}
