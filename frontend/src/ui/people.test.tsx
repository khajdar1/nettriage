import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AiMark } from "./AiMark";
import { Ago } from "./Ago";
import { formatDateTime } from "./format";
import { Person, Unassigned, initials } from "./Person";

afterEach(() => {
  vi.useRealTimers();
});

test("initials come from a display name, or from an email address before the @", () => {
  expect(["Ben Okafor", "ana@acme.example", "cleo.park@acme.example", "x"].map(initials)).toEqual([
    "BO",
    "AN",
    "CP",
    "X",
  ]);
});

test("a person is their initials beside their name, and the signed-in person is You", () => {
  const { container, rerender } = render(<Person name="Ben Okafor" />);

  expect(screen.getByText("Ben Okafor")).toBeInTheDocument();
  expect(container.querySelector(".initials")).toHaveTextContent("BO");
  expect(container.querySelector(".initials")).toHaveAttribute("aria-hidden", "true");

  rerender(<Person name="ana@acme.example" you />);
  expect(screen.getByText("You")).toBeInTheDocument();
  expect(screen.queryByText("ana@acme.example")).toBeNull();
});

test("nobody assigned reads as Unassigned", () => {
  render(<Unassigned />);

  expect(screen.getByText("Unassigned")).toBeInTheDocument();
});

test("a moment shows how long ago it was, with the exact time to hover", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00Z"));

  render(<Ago iso="2026-10-05T10:00:00Z" />);

  const time = screen.getByText("2 h ago");
  expect(time.tagName).toBe("TIME");
  expect(time).toHaveAttribute("dateTime", "2026-10-05T10:00:00Z");
  expect(time).toHaveAttribute("title", formatDateTime("2026-10-05T10:00:00Z"));
});

test("a finding's AI mark says whether it's explained", () => {
  const { rerender } = render(<AiMark status="succeeded" />);
  expect(screen.getByText("Explained")).toBeInTheDocument();

  for (const status of [null, "pending"] as const) {
    rerender(<AiMark status={status} />);
    expect(screen.getByText("Not explained yet")).toBeInTheDocument();
  }
  for (const status of ["failed", "skipped_budget", "invalid_output"] as const) {
    rerender(<AiMark status={status} />);
    expect(screen.getByText("No explanation")).toBeInTheDocument();
  }
});
