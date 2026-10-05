import { useId } from "react";
import { formatDay, formatUsd } from "../../ui/format";

const BAR = 10;
const STEP = 14;
const HEIGHT = 120;

/**
 * The AI's cost per UTC day as bars in ink (the owner's decision, Plan 6b); a day without calls is
 * a gap. The table below it holds the same numbers for screen readers.
 */
export function UsageChart({ series }: { series: { day: string; cost: number }[] }) {
  const captionId = useId();
  const highest = Math.max(...series.map((point) => point.cost));
  const width = series.length * STEP;
  const first = series[0]?.day ?? "";
  const last = series.at(-1)?.day ?? "";
  return (
    <figure className="usage-chart" aria-labelledby={captionId}>
      <svg viewBox={`0 0 ${width} ${HEIGHT + 2}`} aria-hidden="true" preserveAspectRatio="none">
        {series.map((point, index) =>
          point.cost > 0 ? (
            <rect
              key={point.day}
              className="bar"
              x={index * STEP + (STEP - BAR) / 2}
              y={HEIGHT - Math.max(2, (point.cost / highest) * HEIGHT)}
              width={BAR}
              height={Math.max(2, (point.cost / highest) * HEIGHT)}
            />
          ) : null,
        )}
        <line className="baseline" x1={0} y1={HEIGHT + 1} x2={width} y2={HEIGHT + 1} />
      </svg>
      <figcaption id={captionId}>
        AI cost per UTC day, {formatDay(first)} to {formatDay(last)}. The busiest day cost{" "}
        {formatUsd(String(highest))}.
      </figcaption>
    </figure>
  );
}
