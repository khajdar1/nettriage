import { useId, useLayoutEffect, useMemo, useRef } from "react";

/**
 * A host's ports 0 to 1023 as a 32 by 32 grid, with the probed ones lit (the look's one bold
 * element). It is drawn finished. With `sweep`, and only where motion is allowed, it replays the
 * probes in order once: the page's single orchestrated moment.
 */

const COLS = 32;
const PORTS = 1024;
const PITCH = 16;
const SIZE = 14;
const ROW_TICKS = [0, 8, 16, 24, 31];
const RIGHT = COLS * PITCH + 10;
const STEP_MS = 14;

export interface Callout {
  port: number;
  name: string;
}

function motionAllowed(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** The ports on the map, each once, in the order they were first probed. */
function probeOrder(signature: string): number[] {
  const order = new Set<number>();
  for (const part of signature.split(",")) {
    const port = Number(part);
    if (part !== "" && Number.isInteger(port) && port >= 0 && port < PORTS) {
      order.add(port);
    }
  }
  return [...order];
}

function x(port: number): number {
  return (port % COLS) * PITCH;
}

function y(port: number): number {
  return Math.floor(port / COLS) * PITCH;
}

export function PortMap({
  ports,
  caption,
  callouts = [],
  sweep = false,
  tallyLabel = "probed",
}: {
  ports: readonly number[];
  caption: string;
  callouts?: Callout[];
  sweep?: boolean;
  /** What the count beside the caption counts: "150 probed", or "50 in the sample". */
  tallyLabel?: string;
}) {
  const grid = useRef<SVGSVGElement>(null);
  const count = useRef<HTMLElement>(null);
  const captionId = useId();
  // Keyed by the ports' values, so a new list of the same probes doesn't replay the sweep.
  const signature = ports.join(",");
  const order = useMemo(() => probeOrder(signature), [signature]);
  const probed = new Set(order);

  // Before the first paint, so a sweep never flashes the finished map first. The count is written
  // here, not rendered, because the sweep rewrites it as it goes.
  useLayoutEffect(() => {
    const svg = grid.current;
    const counter = count.current;
    if (svg === null || counter === null) {
      return;
    }
    // Reads what's probed from the drawing, which already shows the newest ports when this runs.
    const finish = () => {
      const lit = svg.querySelectorAll("rect.cell[data-probed]");
      for (const cell of svg.querySelectorAll("rect.cell.now")) {
        cell.classList.remove("now");
      }
      for (const cell of lit) {
        cell.classList.add("lit");
      }
      svg.classList.remove("sweeping");
      counter.textContent = String(lit.length);
    };
    if (!sweep || !motionAllowed()) {
      finish();
      return;
    }
    const cells = svg.querySelectorAll("rect.cell");
    const steps = order.map((port) => cells[port]).filter((cell) => cell !== undefined);
    for (const cell of steps) {
      cell.classList.remove("lit");
    }
    svg.classList.add("sweeping");
    counter.textContent = "0";
    let next = 0;
    let timer = 0;
    const tick = () => {
      steps[next - 1]?.classList.replace("now", "lit");
      const cell = steps[next];
      if (cell === undefined) {
        finish();
        return;
      }
      cell.classList.add("now");
      next += 1;
      counter.textContent = String(next);
      timer = window.setTimeout(tick, STEP_MS);
    };
    timer = window.setTimeout(tick, 450);
    return () => {
      window.clearTimeout(timer);
      finish();
    };
  }, [order, sweep]);

  return (
    <figure className="port-map" aria-labelledby={captionId}>
      <svg ref={grid} className="port-map-grid" viewBox="-30 -18 634 560" aria-hidden="true">
        {Array.from({ length: PORTS }, (_, port) => (
          <rect
            key={port}
            data-port={port}
            data-probed={probed.has(port) ? "" : undefined}
            className={probed.has(port) ? "cell lit" : "cell"}
            x={x(port)}
            y={y(port)}
            width={SIZE}
            height={SIZE}
          />
        ))}
        {ROW_TICKS.map((row) => (
          <text key={row} className="tick" x={-6} y={row * PITCH + 11} textAnchor="end">
            {row * COLS}
          </text>
        ))}
        <text className="tick" x={0} y={-6}>
          +0
        </text>
        <text className="tick" x={(COLS - 1) * PITCH + SIZE} y={-6} textAnchor="end">
          +31
        </text>
        {callouts.map(({ port, name }) => (
          <g key={port}>
            <rect
              className="ring"
              x={x(port) - 2}
              y={y(port) - 2}
              width={SIZE + 4}
              height={SIZE + 4}
            />
            <line
              className="lead"
              x1={x(port) + SIZE + 2}
              y1={y(port) + SIZE / 2}
              x2={RIGHT}
              y2={y(port) + SIZE / 2}
            />
            <text className="call" x={RIGHT + 4} y={y(port) + SIZE / 2 + 4}>
              <tspan className="port">{port}</tspan> {name}
            </text>
          </g>
        ))}
      </svg>
      <figcaption>
        <span id={captionId}>{caption}</span>
        <span>
          <b ref={count} /> {tallyLabel}
        </span>
      </figcaption>
    </figure>
  );
}
