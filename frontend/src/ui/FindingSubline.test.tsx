import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { findingSummary } from "../test/fixtures";
import { FindingSubline, flowOf } from "./FindingSubline";

test("a flow reads from source to destination, with the port when there is one", () => {
  expect(flowOf(findingSummary({ dst_port: 22, dst_ip: "10.0.0.12", src_ip: "10.0.4.9" }))).toBe(
    "10.0.4.9 → 10.0.0.12:22",
  );
  expect(flowOf(findingSummary({ dst_port: null }))).toBe("10.0.3.17 → 10.0.0.5");
});

test("one port probed on many hosts has no single destination", () => {
  expect(flowOf(findingSummary({ dst_ip: null, dst_port: 445 }))).toBe(
    "10.0.3.17 → many hosts:445",
  );
});

test("IPv6 addresses are bracketed so the port stays readable", () => {
  expect(
    flowOf(findingSummary({ src_ip: "2001:db8::9", dst_ip: "2001:db8::5", dst_port: 22 })),
  ).toBe("[2001:db8::9] → [2001:db8::5]:22");
});

test("the subline names the detector and whether the AI explained the finding", () => {
  render(<FindingSubline finding={findingSummary({ ai_status: "succeeded" })} />);

  expect(screen.getByText("Port scan")).toBeInTheDocument();
  expect(screen.getByText("10.0.3.17 → 10.0.0.5")).toBeInTheDocument();
  expect(screen.getByText("Explained")).toBeInTheDocument();
});
