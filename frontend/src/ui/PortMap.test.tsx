import { act, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { PortMap } from "./PortMap";

const CAPTION = "Ports 0 to 1023 of 10.0.0.5, lit in the order they were probed";

function cells(container: HTMLElement): SVGRectElement[] {
  return [...container.querySelectorAll<SVGRectElement>("rect.cell")];
}

function litPorts(container: HTMLElement): number[] {
  return cells(container)
    .filter((cell) => cell.classList.contains("lit"))
    .map((cell) => Number(cell.dataset.port));
}

/** The caption's "N probed". */
function tally(container: HTMLElement): string | null | undefined {
  return container.querySelector("figcaption b")?.textContent;
}

function grid(container: HTMLElement): SVGSVGElement {
  const svg = container.querySelector("svg");
  if (svg === null) throw new Error("no map");
  return svg;
}

/** A browser that allows motion (or asks to reduce it), with a clock the test moves. */
function motion({ reduce }: { reduce: boolean }) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query === "(prefers-reduced-motion: reduce)",
  }));
  vi.useFakeTimers();
}

afterEach(() => {
  vi.useRealTimers();
});

test("a port map draws ports 0 to 1023 and lights the probed ones", () => {
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} />);

  expect(cells(container)).toHaveLength(1024);
  expect(litPorts(container)).toEqual([22, 80, 443]);
});

test("it says what it shows in words, and keeps the drawing from screen readers", () => {
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} />);

  expect(screen.getByRole("figure", { name: CAPTION })).toBeInTheDocument();
  expect(tally(container)).toBe("3");
  expect(grid(container)).toHaveAttribute("aria-hidden", "true");
});

test("a port probed twice, or one outside 0 to 1023, is drawn and counted once or not at all", () => {
  const { container } = render(<PortMap ports={[22, 22, 80, 5000, -1]} caption={CAPTION} />);

  expect(litPorts(container)).toEqual([22, 80]);
  expect(tally(container)).toBe("2");
});

test("its count can say what it counts", () => {
  const { container } = render(
    <PortMap ports={[22, 80, 443]} caption={CAPTION} tallyLabel="in the sample" />,
  );

  expect(container.querySelector("figcaption")).toHaveTextContent("3 in the sample");
});

test("well-known ports are named beside the map", () => {
  const { container } = render(
    <PortMap
      ports={[22, 80, 443]}
      caption={CAPTION}
      callouts={[
        { port: 22, name: "ssh" },
        { port: 443, name: "https" },
      ]}
    />,
  );

  const labels = [...container.querySelectorAll("text.call")].map((label) => label.textContent);
  expect(labels).toEqual(["22 ssh", "443 https"]);
});

test("without matchMedia (as in tests) the map is drawn finished, not swept", () => {
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />);

  expect(litPorts(container)).toHaveLength(3);
  expect(grid(container)).not.toHaveClass("sweeping");
});

test("for someone who asks for reduced motion, the map is drawn finished", () => {
  motion({ reduce: true });
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />);

  expect(litPorts(container)).toEqual([22, 80, 443]);
  expect(grid(container)).not.toHaveClass("sweeping");
  expect(vi.getTimerCount()).toBe(0);
});

test("where motion is allowed, the map lights each probe in turn once, then rests finished", () => {
  motion({ reduce: false });
  const { container } = render(<PortMap ports={[443, 22, 80]} caption={CAPTION} sweep />);

  expect(litPorts(container)).toEqual([]);
  expect(grid(container)).toHaveClass("sweeping");
  expect(tally(container)).toBe("0");

  act(() => vi.runAllTimers());

  expect(litPorts(container)).toEqual([22, 80, 443]);
  expect(grid(container)).not.toHaveClass("sweeping");
  expect(tally(container)).toBe("3");
  expect(vi.getTimerCount()).toBe(0);
});

test("leaving the page mid-sweep leaves no timer running", () => {
  motion({ reduce: false });
  const { unmount } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />);
  act(() => vi.advanceTimersByTime(470));

  unmount();

  expect(vi.getTimerCount()).toBe(0);
});

test("React's strict mode, which mounts twice, still ends fully lit", () => {
  motion({ reduce: false });
  const { container } = render(
    <StrictMode>
      <PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />
    </StrictMode>,
  );

  act(() => vi.runAllTimers());

  expect(litPorts(container)).toEqual([22, 80, 443]);
  expect(tally(container)).toBe("3");
});

test("new probes mid-sweep end with only the new ones lit and counted", () => {
  motion({ reduce: false });
  const { container, rerender } = render(
    <PortMap ports={[1, 2, 3, 4, 5]} caption={CAPTION} sweep />,
  );
  act(() => vi.advanceTimersByTime(480));

  rerender(<PortMap ports={[10, 20]} caption={CAPTION} sweep />);
  act(() => vi.runAllTimers());

  expect(litPorts(container)).toEqual([10, 20]);
  expect(tally(container)).toBe("2");
});

test("new probes without a sweep are drawn and counted at once", () => {
  motion({ reduce: false });
  const { container, rerender } = render(
    <PortMap ports={[1, 2, 3, 4, 5]} caption={CAPTION} sweep />,
  );
  act(() => vi.advanceTimersByTime(480));

  rerender(<PortMap ports={[10, 20]} caption={CAPTION} />);

  expect(litPorts(container)).toEqual([10, 20]);
  expect(tally(container)).toBe("2");
  expect(grid(container)).not.toHaveClass("sweeping");
});

test("the same probes in a new list don't replay the sweep", () => {
  motion({ reduce: false });
  const { container, rerender } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />);
  act(() => vi.runAllTimers());

  rerender(<PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />);

  expect(grid(container)).not.toHaveClass("sweeping");
  expect(litPorts(container)).toEqual([22, 80, 443]);
});
