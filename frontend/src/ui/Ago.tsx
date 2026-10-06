import { formatAgo, formatDateTime } from "./format";

/** A moment as how long ago it was, with the exact local time on hover (Plan 6d). */
export function Ago({ iso }: { iso: string }) {
  return (
    <time className="ago" dateTime={iso} title={formatDateTime(iso)}>
      {formatAgo(iso, Date.now())}
    </time>
  );
}
