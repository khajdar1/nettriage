/** A finding's severity as one to four lit bars, always shown beside its word (never alone). */
export function SeverityBars({ level }: { level: number }) {
  return (
    <span className="sev" aria-hidden="true">
      {[1, 2, 3, 4].map((bar) => (
        <i key={bar} className={bar <= level ? "on" : undefined} />
      ))}
    </span>
  );
}
