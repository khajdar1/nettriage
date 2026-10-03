import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { PortMap } from "./PortMap";

const CAPTION = "Ports 0 to 1023 of 10.0.0.5, lit in the order they were probed";

function cells(container: HTMLElement): SVGRectElement[] {
  return [...container.querySelectorAll<SVGRectElement>("rect.cell")];
}

test("a port map draws ports 0 to 1023 and lights the probed ones", () => {
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} />);

  const all = cells(container);
  expect(all).toHaveLength(1024);
  expect(
    all.filter((cell) => cell.classList.contains("lit")).map((cell) => cell.dataset.port),
  ).toEqual(["22", "80", "443"]);
});

test("it says what it shows in words, and keeps the drawing from screen readers", () => {
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} />);

  expect(screen.getByRole("figure", { name: CAPTION })).toBeInTheDocument();
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
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

test("without motion (and in tests) the map is drawn finished, not swept", () => {
  const { container } = render(<PortMap ports={[22, 80, 443]} caption={CAPTION} sweep />);

  expect(cells(container).filter((cell) => cell.classList.contains("lit"))).toHaveLength(3);
  expect(container.querySelector("svg")).not.toHaveClass("sweeping");
});
