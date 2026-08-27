/**
 * variation.ts — deterministic seeded variation (§8 "seeded variation via
 * `seed`", §15 "facade seeds are honest controls").
 *
 * THE CONTRACT: same `(seed, key)` → same pick, on every machine, forever.
 * A facade seed the user can type into a box is a promise — "seed 7 looks like
 * this" — and the promise only holds if nothing here depends on `Math.random`,
 * locale, iteration order of a Map, or float rounding. So:
 *
 *  - the hash is FNV-1a over the UTF-16 code units of `"key#seed"`, pure
 *    32-bit integer arithmetic via `Math.imul` — IEEE-754 plays no part;
 *  - variant selection is `hash % count` — no float division, no bias games
 *    (the count is 2–4; the modulo bias over 2^32 is ~one part in a billion,
 *    which is not what breaks facades);
 *  - every call site passes a SEMANTIC key ("chajja-projection", a wall id),
 *    never an array index, so inserting a wall does not reshuffle every other
 *    wall's variant.
 *
 * Variation policy note: variants that must read as one design decision
 * (chajja projection, colorway) are picked ONCE per building with a fixed key;
 * per-element keys exist for future kits that want controlled per-bay rhythm.
 */

/** 32-bit FNV-1a over the code units of `text`. Deterministic, allocation-free. */
export function fnv1a32(text: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  // >>> 0 folds the signed imul result into an unsigned 32-bit integer.
  return hash >>> 0;
}

/**
 * Deterministic index into `count` variants for `(seed, key)`.
 * `count <= 0` returns 0 so a degenerate variant list cannot throw mid-render.
 */
export function variantIndex(seed: number, key: string, count: number): number {
  if (count <= 1) return 0;
  return fnv1a32(`${key}#${String(seed)}`) % count;
}

/**
 * Deterministic pick from a non-empty list; `fallback` when the list is empty.
 * The generic keeps the pick typed without a non-null assertion at call sites
 * (`noUncheckedIndexedAccess` — inherited fact 5).
 */
export function pickVariant<T>(seed: number, key: string, variants: readonly T[], fallback: T): T {
  const picked = variants[variantIndex(seed, key, variants.length)];
  return picked ?? fallback;
}

/**
 * The panel's "shuffle" step: a full-period 32-bit LCG (Numerical Recipes
 * constants), so repeated shuffles walk every seed once before repeating and
 * the same shuffle from the same seed always lands on the same next seed.
 * Honest control: the panel SHOWS the resulting number and lets the user type
 * their own.
 */
export function nextSeed(seed: number): number {
  return (Math.imul(seed >>> 0, 1664525) + 1013904223) >>> 0;
}
