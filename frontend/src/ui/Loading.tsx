/** Shown while a page's data loads; screen readers announce it once. */
export function Loading() {
  return (
    <p role="status" className="muted">
      Loading…
    </p>
  );
}
