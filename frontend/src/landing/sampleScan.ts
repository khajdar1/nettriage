/**
 * The landing page's example: the 150 ports of 10.0.0.5 that 203.0.113.9 probed, in the shuffled
 * order a scanner tries them. Seeded, so the page is the same on every load.
 */

// Services every scanner tries first.
const WELL_KNOWN = [
  21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 389, 443, 445, 465, 587, 636, 873, 993, 995,
];

/** mulberry32: a small 32-bit generator, exact in JavaScript's numbers. */
function seeded(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function scan(): number[] {
  const random = seeded(2026);
  const ports = new Set(WELL_KNOWN);
  while (ports.size < 150) {
    ports.add(1 + Math.floor(random() * 1023));
  }
  const order = [...ports];
  for (let i = order.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    const swap = order[i] as number;
    order[i] = order[j] as number;
    order[j] = swap;
  }
  return order;
}

export const SAMPLE_SCAN: readonly number[] = scan();
