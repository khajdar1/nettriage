import { expect, test } from "vitest";
import { filtersFrom, searchFrom } from "./findings";

test("findings open most severe first, with no filter", () => {
  expect(filtersFrom(new URLSearchParams())).toEqual({ sort: "severity" });
});

test("filters and the order are read from the address", () => {
  const search = new URLSearchParams(
    "severity=high&status=investigating&detector=port_scan&upload=u1&sort=newest",
  );

  expect(filtersFrom(search)).toEqual({
    severity: "high",
    status: "investigating",
    detector: "port_scan",
    upload: "u1",
    sort: "newest",
  });
});

test("a value that isn't real is read as no filter", () => {
  expect(filtersFrom(new URLSearchParams("severity=urgent&status=closed&sort=oldest"))).toEqual({
    sort: "severity",
  });
});

test("the address keeps only what differs from the defaults", () => {
  expect(searchFrom({ sort: "severity" }).toString()).toBe("");
  expect(searchFrom({ severity: "low", sort: "newest" }).toString()).toBe(
    "severity=low&sort=newest",
  );
});
