import { expect, test } from "vitest";
import { SAMPLE_SCAN } from "./sampleScan";

test("the sample scan is 150 distinct ports below 1024, in the order a scanner tried them", () => {
  expect(SAMPLE_SCAN).toHaveLength(150);
  expect(new Set(SAMPLE_SCAN).size).toBe(150);
  expect(SAMPLE_SCAN.every((port) => Number.isInteger(port) && port >= 1 && port <= 1023)).toBe(
    true,
  );
});

test("it probes the well-known services a scanner always tries, and not in port order", () => {
  expect(SAMPLE_SCAN).toEqual(expect.arrayContaining([22, 80, 443]));
  expect([...SAMPLE_SCAN].sort((a, b) => a - b)).not.toEqual(SAMPLE_SCAN);
});
