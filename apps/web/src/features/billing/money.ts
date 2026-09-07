/**
 * Money on the billing pages. Integers in, strings out — the same rule as the ledger.
 *
 * The API keeps every charge in micro-USD (millionths of a dollar, integer) because
 * that is the unit the providers bill in. `formatUsd` mirrors `spend.format_usd` on the
 * server so a number shown here and a number shown in a 402 message cannot differ.
 */

export const MICROS_PER_USD = 1_000_000;

/** `4_250_000` → "$4.25". Cents floored, like the server. */
export function formatUsd(micros: number): string {
  const safe = Math.max(0, Math.trunc(micros));
  const whole = Math.floor(safe / MICROS_PER_USD);
  const cents = Math.floor(((safe % MICROS_PER_USD) * 100) / MICROS_PER_USD);
  return `$${whole}.${String(cents).padStart(2, '0')}`;
}
