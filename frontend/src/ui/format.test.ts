import { expect, test } from "vitest";
import { formatClock, formatDateTime, formatDay, formatTime, formatUsd } from "./format";

test("a moment is written with its date and its time", () => {
  const written = formatDateTime("2026-10-03T14:05:00Z");

  expect(written).toMatch(/2026/);
  expect(written).toMatch(/:05/);
});

test("AI costs keep the fractions of a cent they are made of", () => {
  expect(["0", "0.0123", "0.00012345", "12.5"].map(formatUsd)).toEqual([
    "$0.00",
    "$0.0123",
    "$0.0001",
    "$12.50",
  ]);
  expect(formatUsd("0.00004")).toBe("under $0.0001");
});

test("times of day are written to the minute, or to the second for flows", () => {
  expect(formatTime("2026-10-03T14:05:09Z")).toMatch(/:05/);
  expect(formatTime("2026-10-03T14:05:09Z")).not.toMatch(/:09/);
  expect(formatClock("2026-10-03T14:05:09Z")).toMatch(/:05:09/);
});

test("a UTC day is written as that day, whatever the time zone", () => {
  expect(formatDay("2026-10-04")).toMatch(/4/);
  expect(formatDay("2026-10-04")).not.toMatch(/3/);
});
