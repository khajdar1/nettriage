import { expect, test } from "vitest";
import { dailyCost, lastDays, periodFrom } from "./usage";

const NOW = Date.parse("2026-10-04T23:30:00Z");

test("the last days are UTC days, oldest first, ending today", () => {
  expect(lastDays(3, NOW)).toEqual(["2026-10-02", "2026-10-03", "2026-10-04"]);
});

test("each day of the period has its cost, and a day without calls costs nothing", () => {
  const usage = {
    days: [
      { day: "2026-10-04", calls: 3, input_tokens: 2700, output_tokens: 900, cost_usd: "0.0009" },
      { day: "2026-10-02", calls: 1, input_tokens: 900, output_tokens: 300, cost_usd: "0.0003" },
    ],
    totals: { calls: 4, input_tokens: 3600, output_tokens: 1200, cost_usd: "0.0012" },
  };

  expect(dailyCost(usage, 3, NOW)).toEqual([
    { day: "2026-10-02", cost: 0.0003 },
    { day: "2026-10-03", cost: 0 },
    { day: "2026-10-04", cost: 0.0009 },
  ]);
});

test("the period is 7, 30 or 90 days, and 30 unless the address says otherwise", () => {
  expect(["7", "90", "30", "45", null].map(periodFrom)).toEqual([7, 90, 30, 30, 30]);
});
