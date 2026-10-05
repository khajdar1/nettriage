import { expect, test } from "vitest";
import { apiQuery, filtersFrom, searchFrom } from "./findings";

test("findings open on the unresolved ones, most severe first, with no filter", () => {
  expect(filtersFrom(new URLSearchParams())).toEqual({ sort: "severity" });
});

test("filters and the order are read from the address", () => {
  const search = new URLSearchParams(
    "severity=high&status=investigating&assignee=me&new=day&detector=port_scan&upload=u1&sort=newest",
  );

  expect(filtersFrom(search)).toEqual({
    severity: "high",
    status: "investigating",
    assignee: "me",
    new: "day",
    detector: "port_scan",
    upload: "u1",
    sort: "newest",
  });
  expect(filtersFrom(new URLSearchParams("status=any&assignee=none"))).toEqual({
    status: "any",
    assignee: "none",
    sort: "severity",
  });
});

test("a value that isn't real is read as no filter", () => {
  expect(
    filtersFrom(
      new URLSearchParams("severity=urgent&status=closed&assignee=bob&new=week&sort=oldest"),
    ),
  ).toEqual({ sort: "severity" });
});

test("the address keeps only what differs from the defaults", () => {
  expect(searchFrom({ sort: "severity" }).toString()).toBe("");
  expect(searchFrom({ severity: "low", sort: "newest" }).toString()).toBe(
    "severity=low&sort=newest",
  );
  expect(
    searchFrom({ status: "any", new: "day", assignee: "none", sort: "severity" }).toString(),
  ).toBe("status=any&assignee=none&new=day");
});

const NOW = Date.parse("2026-10-05T12:00:00Z");

test("unresolved, the default, asks the API for Open and Investigating findings", () => {
  expect(apiQuery({ sort: "severity" }, NOW)).toEqual({
    sort: "severity",
    status: ["open", "investigating"],
  });
});

test("any status sends no status, and one status sends just that one", () => {
  expect(apiQuery({ status: "any", sort: "severity" }, NOW)).toEqual({ sort: "severity" });
  expect(apiQuery({ status: "resolved", sort: "newest" }, NOW)).toEqual({
    sort: "newest",
    status: ["resolved"],
  });
});

test("new in the last day asks for findings since 24 hours ago, beside the other filters", () => {
  expect(
    apiQuery(
      {
        status: "any",
        new: "day",
        assignee: "me",
        severity: "high",
        detector: "port_scan",
        upload: "u1",
        sort: "severity",
      },
      NOW,
    ),
  ).toEqual({
    sort: "severity",
    since: "2026-10-04T12:00:00.000Z",
    assignee: "me",
    severity: "high",
    detector: "port_scan",
    upload: "u1",
  });
});
