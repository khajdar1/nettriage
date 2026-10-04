import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { SeverityBars } from "./SeverityBars";

test.each([
  [1, 1],
  [3, 3],
  [4, 4],
])("severity %i lights %i of four bars, beside its word for screen readers", (level, lit) => {
  const { container } = render(<SeverityBars level={level} />);

  const bars = container.querySelectorAll(".sev i");
  expect(bars).toHaveLength(4);
  expect(container.querySelectorAll(".sev i.on")).toHaveLength(lit);
  expect(container.querySelector(".sev")).toHaveAttribute("aria-hidden", "true");
});
