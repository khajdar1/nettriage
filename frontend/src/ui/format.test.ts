import { expect, test } from "vitest";
import {
  formatAgo,
  formatClock,
  formatDate,
  formatDateTime,
  formatDay,
  formatTime,
  formatUsd,
  formatWindow,
} from "./format";

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

test("a window within one day is written as two times, and one across days with its dates", () => {
  const start = "2026-10-01T12:00:00Z";
  const end = "2026-10-01T12:05:00Z";
  expect(formatWindow(start, end)).toBe(`${formatTime(start)} to ${formatTime(end)}`);
  const later = "2026-10-03T12:05:00Z";
  expect(formatWindow(start, later)).toBe(`${formatDateTime(start)} to ${formatDateTime(later)}`);
});

test("a recent moment is said as how long ago it was, and an older one as its date", () => {
  const now = Date.parse("2026-10-05T12:00:00Z");
  const ago = (iso: string) => formatAgo(iso, now);

  expect(ago("2026-10-05T11:59:30Z")).toBe("just now");
  expect(ago("2026-10-05T12:05:00Z")).toBe("just now");
  expect(ago("2026-10-05T11:59:00Z")).toBe("1 min ago");
  expect(ago("2026-10-05T11:01:00Z")).toBe("59 min ago");
  expect(ago("2026-10-05T11:00:00Z")).toBe("1 h ago");
  expect(ago("2026-10-04T12:00:01Z")).toBe("23 h ago");
});

test("a day or more ago is counted in calendar days, so yesterday is always the day before", () => {
  // Local times, so the calendar is the person's whatever the time zone. 5 October 2026 is a Monday.
  const at = (month: number, day: number, hour: number) =>
    new Date(2026, month - 1, day, hour).toISOString();
  const ago = (iso: string) => formatAgo(iso, Date.parse(at(10, 5, 9)));

  expect(ago(at(10, 4, 23))).toBe("10 h ago");
  expect(ago(at(10, 4, 8))).toBe("yesterday");
  expect(ago(at(10, 3, 10))).toBe("2 days ago");
  expect(ago(at(9, 29, 10))).toBe("6 days ago");
  expect(ago(at(9, 28, 23))).toBe(formatDate(at(9, 28, 23)));
});
