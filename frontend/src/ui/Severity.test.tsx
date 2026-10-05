import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Severity } from "./Severity";

test("a severity is its word, with its bars beside it for sighted readers", () => {
  const { container } = render(<Severity severity="high" />);

  expect(screen.getByText("High")).toBeVisible();
  expect(container.querySelectorAll(".sev i.on")).toHaveLength(3);
});
