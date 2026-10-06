/**
 * The palette's contrast (spec §10, WCAG 2.2 AA): text at least 4.5:1, and focus rings and
 * form fields' borders at least 3:1 against what they sit on (WCAG 1.4.3 and 1.4.11).
 */
import { expect, test } from "vitest";
import css from "./styles.css?raw";

const TOKENS = new Map(
  [...css.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})/g)].map(([, name, hex]) => [name, hex]),
);

function color(name: string): string {
  const hex = TOKENS.get(name);
  if (hex === undefined) {
    throw new Error(`no --${name} in styles.css`);
  }
  return hex;
}

function luminance(hex: string): number {
  const [r = 0, g = 0, b = 0] = [1, 3, 5].map((at) => {
    const channel = parseInt(hex.slice(at, at + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

const TEXT_ON: [string, string][] = [
  ["ink", "paper"],
  ["ink", "plate"],
  ["graphite", "paper"],
  ["graphite", "plate"],
  ["signal-ink", "paper"],
  ["signal-ink", "plate"],
  ["teal", "paper"],
  ["teal", "plate"],
  ["danger", "plate"],
  ["plate", "ink"],
  ["plate", "danger"],
  ["ink", "signal"],
];

test("text is at least 4.5:1 on the backgrounds it's used on", () => {
  for (const [text, background] of TEXT_ON) {
    expect(contrast(color(text), color(background)), `${text} on ${background}`).toBeGreaterThan(
      4.5,
    );
  }
});

test("focus rings stand out at 3:1 on every surface", () => {
  expect(contrast(color("teal"), color("paper"))).toBeGreaterThan(3);
  expect(contrast(color("teal"), color("plate"))).toBeGreaterThan(3);
  expect(css).toMatch(/:focus-visible\s*\{\s*outline: 3px solid var\(--teal\);/);
});

test("form fields' borders are at least 3:1 on white, so the fields can be seen", () => {
  expect(contrast(color("field-line"), color("plate"))).toBeGreaterThan(3);
  expect(css).toMatch(
    /\.field input,\s*\.field select\s*\{[^}]*border: 1px solid var\(--field-line\);/,
  );
  expect(css).toMatch(/\.invite-link input\s*\{[^}]*border: 1px solid var\(--field-line\);/);
  expect(css).toMatch(/\.table select\s*\{[^}]*border: 1px solid var\(--field-line\);/);
});

test("amber is the detectors' color: probed ports, severity and the mark, nothing else", () => {
  const uses = [...css.matchAll(/([^{}]+)\{[^}]*var\(--signal\)[^}]*\}/g)].map(([, selector]) =>
    (selector ?? "").trim(),
  );
  expect(uses.length).toBeGreaterThan(0);
  for (const selector of uses) {
    expect(selector, selector).toMatch(/\.(lit|sev|step-detect|logo-lit)(?![\w-])/);
  }
});

test("no grid column needs more room than a 320-pixel screen has (WCAG 1.4.10)", () => {
  // A column's minimum is 0, or a length capped at the room there is: min(19rem, 100%).
  const minimums = [...css.matchAll(/minmax\(((?:[^(),]|\([^()]*\))+),/g)].map(([, minimum]) =>
    (minimum ?? "").trim(),
  );
  expect(minimums.length).toBeGreaterThan(5);
  expect(minimums.filter((minimum) => minimum !== "0" && !minimum.startsWith("min("))).toEqual([]);
});
