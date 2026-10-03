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
  ["text", "paper"],
  ["ink", "paper"],
  ["muted", "card"],
  ["teal-dark", "paper"],
  ["teal", "navy"],
  ["paper", "navy-raised"],
];

test("text is at least 4.5:1 on the backgrounds it's used on", () => {
  for (const [text, background] of TEXT_ON) {
    expect(contrast(color(text), color(background)), `${text} on ${background}`).toBeGreaterThan(
      4.5,
    );
  }
});

test("focus rings stand out at 3:1, in teal on the navy parts of the app", () => {
  expect(contrast(color("teal-dark"), color("paper"))).toBeGreaterThan(3);
  expect(contrast(color("teal"), color("navy"))).toBeGreaterThan(3);
  expect(css).toMatch(
    /\.topbar :focus-visible,\s*\.landing :focus-visible\s*\{\s*outline-color: var\(--teal\);/,
  );
});

test("form fields' borders are at least 3:1 on white, so the fields can be seen", () => {
  expect(contrast(color("field-line"), "#ffffff")).toBeGreaterThan(3);
  expect(css).toMatch(
    /\.field input,\s*\.field select\s*\{[^}]*border: 1px solid var\(--field-line\);/,
  );
  expect(css).toMatch(/\.invite-link input\s*\{[^}]*border: 1px solid var\(--field-line\);/);
});
