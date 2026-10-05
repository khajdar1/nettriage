/** Two letters for a person: from their display name, or from their email before the @. */
export function initials(name: string): string {
  const words =
    name
      .split("@")[0]
      ?.split(/[\s._-]+/)
      .filter((word) => word !== "") ?? [];
  const [first = "", second] = words;
  const letters = second === undefined ? first.slice(0, 2) : `${first[0] ?? ""}${second[0] ?? ""}`;
  return letters.toUpperCase();
}

/** Someone: a disc with their initials beside their name, or You for the signed-in person. */
export function Person({ name, you = false }: { name: string; you?: boolean }) {
  return (
    <span className="person">
      <span className="initials" aria-hidden="true">
        {initials(name)}
      </span>
      {you ? "You" : name}
    </span>
  );
}

/** Nobody assigned: an empty, dashed disc. */
export function Unassigned() {
  return (
    <span className="person">
      <span className="initials initials-none" aria-hidden="true" />
      <span className="muted">Unassigned</span>
    </span>
  );
}
