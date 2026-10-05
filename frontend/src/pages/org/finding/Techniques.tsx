import { useQuery } from "@tanstack/react-query";
import { api } from "../../../api/client";
import { unwrap } from "../../../api/problem";
import type { Technique } from "../../../findings/finding";
import { ExternalLink } from "../../../ui/ExternalLink";

/** MITRE's notice, carried by the API's reference data (spec §7). The same for every technique. */
function useMitreNotice(techniqueId: string | undefined) {
  return useQuery({
    queryKey: ["attack-technique", techniqueId],
    enabled: techniqueId !== undefined,
    staleTime: Infinity,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/attack-techniques/{technique_id}", {
          params: { path: { technique_id: techniqueId ?? "" } },
        }),
      ).notice,
  });
}

interface Named {
  technique: Technique;
  byDetector: boolean;
  byAi: boolean;
  rationale: string | null;
}

/** Each technique once, with who named it: the detector, the AI, or both. */
function named(techniques: Technique[]): Named[] {
  const byId = new Map<string, Named>();
  for (const technique of techniques) {
    const entry = byId.get(technique.id) ?? {
      technique,
      byDetector: false,
      byAi: false,
      rationale: null,
    };
    if (technique.source === "ai") {
      entry.byAi = true;
      entry.rationale = technique.rationale;
    } else {
      entry.byDetector = true;
    }
    byId.set(technique.id, entry);
  }
  return [...byId.values()];
}

function namedBy(entry: Named): string {
  if (entry.byDetector && entry.byAi) {
    return "From the detector and the AI";
  }
  return entry.byAi ? "Suggested by the AI" : "From the detector";
}

/** The finding's ATT&CK techniques (spec §10), linking to attack.mitre.org. */
export function Techniques({ techniques }: { techniques: Technique[] }) {
  const notice = useMitreNotice(techniques[0]?.id);
  return (
    <section className="techniques" aria-labelledby="techniques-title">
      <h2 id="techniques-title">ATT&amp;CK techniques</h2>
      {techniques.length === 0 ? (
        <p className="muted">No ATT&amp;CK technique is linked to this finding.</p>
      ) : (
        <ul>
          {named(techniques).map((entry) => (
            <li key={entry.technique.id}>
              <ExternalLink href={entry.technique.url}>
                <span className="mono">{entry.technique.id}</span> {entry.technique.name}
              </ExternalLink>
              <div className="muted">{namedBy(entry)}</div>
              {entry.rationale !== null && <p>{entry.rationale}</p>}
            </li>
          ))}
        </ul>
      )}
      {notice.data !== undefined && <p className="mitre">{notice.data}</p>}
    </section>
  );
}
