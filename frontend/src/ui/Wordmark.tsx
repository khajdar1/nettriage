import { Link } from "react-router";

// The logo is a tiny port map: a 3 by 3 grid with three ports lit.
const CELLS: [number, number, boolean][] = [
  [0, 0, false],
  [8, 0, false],
  [16, 0, false],
  [0, 8, false],
  [8, 8, true],
  [16, 8, true],
  [0, 16, false],
  [8, 16, true],
  [16, 16, false],
];

/** NetTriage's name and mark, linking home (or to the app, inside it). */
export function Wordmark({ to }: { to: string }) {
  return (
    <Link to={to} className="wordmark">
      <svg className="logo" width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
        {CELLS.map(([x, y, lit]) => (
          <rect
            key={`${x}-${y}`}
            x={x}
            y={y}
            width="6"
            height="6"
            className={lit ? "logo-lit" : "logo-cell"}
          />
        ))}
      </svg>
      NetTriage
    </Link>
  );
}
