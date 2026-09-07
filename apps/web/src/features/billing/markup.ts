/**
 * The platform fee form — what the owner types, what the server is sent, what the page
 * says about the fee in force. Pure functions over `GET /admin/billing/markup`.
 *
 * The one rule that matters: the input is validated to the SAME contract the server
 * enforces (0–100, at most two decimals — whole basis points), so the owner sees the
 * refusal beside the field and never as a 400 after the confirm. The server still
 * decides; this only stops the round trip.
 */

import type { PlatformMarkup } from '../../lib/api';

/** The ceiling, in basis points: 100 %. */
export const MAX_MARKUP_BPS = 10_000;

export interface PercentOk {
  readonly ok: true;
  /** Normalised for display and for the wire: "7.5", not "07.50". */
  readonly percent: string;
  readonly bps: number;
}

export interface PercentBad {
  readonly ok: false;
  readonly reason: string;
}

/** `500` → "5"; `725` → "7.25"; `750` → "7.5". Mirrors `markup.bps_to_percent`. */
export function bpsToPercent(bps: number): string {
  const whole = Math.floor(bps / 100);
  const frac = bps % 100;
  if (frac === 0) return String(whole);
  const digits = String(frac).padStart(2, '0');
  return `${whole}.${digits.endsWith('0') ? digits.slice(0, 1) : digits}`;
}

/**
 * Parse what the owner typed. Accepts "5", "7.25", ".5", "5%", " 12.5 % ". Refuses
 * the empty field, anything that is not a plain decimal, more than two decimals, and
 * anything outside 0–100 — each with the reason the field should show.
 */
export function parsePercentInput(raw: string): PercentOk | PercentBad {
  const text = raw.trim().replace(/\s*%$/u, '');
  if (text === '') return { ok: false, reason: 'Enter a percentage, like 5 or 7.25.' };
  const match = /^(\d*)(?:\.(\d*))?$/u.exec(text);
  if (match === null || (match[1] === '' && (match[2] ?? '') === '')) {
    return { ok: false, reason: 'A percentage is a plain number, like 5 or 7.25.' };
  }
  const whole = match[1] ?? '';
  const frac = match[2] ?? '';
  if (frac.length > 2) {
    return { ok: false, reason: 'At most two decimals — the fee is kept in whole basis points.' };
  }
  const bps = Number(whole === '' ? '0' : whole) * 100 + Number(frac.padEnd(2, '0') || '0');
  if (!Number.isFinite(bps) || bps > MAX_MARKUP_BPS) {
    return { ok: false, reason: 'The fee is between 0 and 100 percent.' };
  }
  return { ok: true, percent: bpsToPercent(bps), bps };
}

/** dd-mm-yyyy, HH:MM in the viewer's zone — the same date shape the usage card uses. */
export function formatWhen(iso: string): string | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const d = new Date(t);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${dd}-${mm}-${d.getFullYear()}, ${hh}:${min}`;
}

/** "Set by owner@firm.in on 07-09-2026, 14:05", or the honest default line. */
export function describeSetBy(markup: PlatformMarkup): string {
  if (markup.source !== 'setting') {
    return 'The boot default from BILLING_MARKUP_PERCENT — no owner has changed it yet.';
  }
  const who = markup.updatedByEmail ?? markup.updatedBy ?? 'a platform owner';
  const when = markup.updatedAt === null ? null : formatWhen(markup.updatedAt);
  return when === null ? `Set by ${who}.` : `Set by ${who} on ${when}.`;
}

/** The confirm dialog's copy: what changes, from when, and what does not change. */
export function describeChange(percent: string): { title: string; description: string } {
  return {
    title: `Change the platform fee to ${percent}%?`,
    description:
      `From the next metered charge, every architect is charged provider cost plus ${percent}%. ` +
      'Charges already recorded keep the fee they were charged at.',
  };
}

/** True when the owner typed the fee that is already in force — nothing to change. */
export function isUnchanged(markup: PlatformMarkup, parsed: PercentOk): boolean {
  return parsed.bps === markup.bps;
}
