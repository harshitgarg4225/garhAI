/**
 * Money on the billing pages. Integers in, strings out — the same rule as the ledger.
 *
 * The API keeps every charge in micro-USD (millionths of a dollar, integer) because
 * that is the unit the providers bill in. Rupees are DERIVED here, at display time,
 * from the one dated rate the usage response carries (`usdInrRate` /
 * `usdInrRateAsOf`, hand-set on the server, never fetched live). Nothing here is a
 * float: the rate is scaled to an integer, the product is a BigInt, and the single
 * rounding is half-up at the very end — `micros_to_paise` in the API's `billing/fx.py`
 * is the reference and `money.test.ts` pins the same table of cases against it.
 */

export const MICROS_PER_USD = 1_000_000;
export const PAISE_PER_INR = 100;
/** The most decimals a rate may carry; the server refuses more at boot. */
export const RATE_MAX_DECIMALS = 4;
const RATE_SCALE = 10n ** BigInt(RATE_MAX_DECIMALS);

/** `4_250_000` → "$4.25". Cents floored, like the server's `format_usd`. */
export function formatUsd(micros: number): string {
  const safe = Math.max(0, Math.trunc(micros));
  const whole = Math.floor(safe / MICROS_PER_USD);
  const cents = Math.floor(((safe % MICROS_PER_USD) * 100) / MICROS_PER_USD);
  return `$${whole}.${String(cents).padStart(2, '0')}`;
}

/**
 * "84.00" → the rate × 10⁴ as a BigInt (840000n), or null for anything that is not a
 * positive plain decimal with at most four places. A null rate means "show dollars":
 * an older api, or a value the server would itself have refused.
 */
export function parseRate(rate: string): bigint | null {
  const match = /^\s*(\d+)(?:\.(\d{0,4}))?\s*$/u.exec(rate);
  if (match === null) return null;
  const whole = match[1] ?? '0';
  const frac = (match[2] ?? '').padEnd(RATE_MAX_DECIMALS, '0');
  const scaled = BigInt(whole) * RATE_SCALE + BigInt(frac === '' ? '0' : frac);
  return scaled > 0n ? scaled : null;
}

/** Micro-dollars → paise at `rate` (from {@link parseRate}), rounded half-up once. */
export function microsToPaise(micros: number, rate: bigint): number {
  if (!Number.isFinite(micros) || micros <= 0) return 0;
  const numerator = BigInt(Math.trunc(micros)) * rate * BigInt(PAISE_PER_INR);
  const denominator = BigInt(MICROS_PER_USD) * RATE_SCALE;
  return Number((numerator * 2n + denominator) / (denominator * 2n));
}

/** `12_345_678` → "₹1,23,456.78" — Indian grouping, always two decimals. */
export function formatInr(paise: number): string {
  const safe = Math.max(0, Math.trunc(paise));
  const whole = Math.floor(safe / PAISE_PER_INR);
  const frac = safe % PAISE_PER_INR;
  let digits = String(whole);
  if (digits.length > 3) {
    let head = digits.slice(0, -3);
    const tail = digits.slice(-3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head !== '') groups.unshift(head);
    digits = [...groups, tail].join(',');
  }
  return `₹${digits}.${String(frac).padStart(2, '0')}`;
}

/** Rupees for `micros` at `rate`, or null when there is no usable rate. */
export function inrFor(micros: number, rate: string): string | null {
  const scaled = parseRate(rate);
  return scaled === null ? null : formatInr(microsToPaise(micros, scaled));
}

/** "₹84.00 per $ (rate of 2026-09-01)" — the source line every rupee carries. */
export function describeRate(rate: string, asOf: string): string | null {
  const scaled = parseRate(rate);
  if (scaled === null) return null;
  // Re-render the rate from its scaled form so "84" and "84.00" read the same.
  const whole = scaled / RATE_SCALE;
  const frac = String(scaled % RATE_SCALE)
    .padStart(RATE_MAX_DECIMALS, '0')
    .replace(/0+$/u, '');
  const text = frac === '' ? `${whole}.00` : `${whole}.${frac.padEnd(2, '0')}`;
  const when = asOf.trim() === '' ? '' : ` (rate of ${asOf.trim()})`;
  return `₹${text} per $${when}`;
}
