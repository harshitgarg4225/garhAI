/**
 * The display boundary. "mm in, pretty out" (golden rule 6).
 *
 * Everything that converts between integer millimetres and something a human
 * reads goes through this module, and every conversion it offers is re-exported
 * from `@garh/model` rather than reimplemented. That indirection is the whole
 * point: `packages/model/src/units.ts` is golden-tested against its Python twin
 * (67 input/output pairs plus 16 must-fail inputs), and a component that
 * hand-rolls `mm / 304.8` gets a number that disagrees with the drawing set.
 *
 * What this module *adds* on top is the Indian display defaults from §15 —
 * ft-in primary with gaj for plot area, ₹ with lakh/crore grouping, DD-MM-YYYY
 * dates, +91 phone formatting — plus the two snap constants the canvas tools
 * share.
 *
 * There is no `parseFloat` anywhere in the app outside this file. If you find
 * yourself wanting one for a length, you want {@link parseLengthMm}.
 */

export {
  // Constants
  MM_PER_INCH,
  MM_PER_FOOT,
  MM_PER_YARD,
  MM_PER_METRE,
  MM_PER_CM,
  MM2_PER_SQFT,
  MM2_PER_SQM,
  MM2_PER_GAJ,
  // Integer-mm discipline
  roundHalfAwayFromZero,
  roundMm,
  isIntMm,
  assertIntMm,
  // Parsing — accepts 12'6", 12ft 6in, 3.8m, 3800, 380cm, …
  normaliseLengthInput,
  parseLengthMm,
  tryParseLengthMm,
  parseAreaMm2,
  UnitParseError,
  // Length formatting
  formatFtIn,
  formatMetres,
  formatMm,
  formatLength,
  // Area conversion + formatting
  toSqft,
  toSqm,
  toGaj,
  fromSqft,
  fromSqm,
  fromGaj,
  formatFixed,
  formatSqft,
  formatSqm,
  formatGaj,
  formatArea,
  formatPlotArea,
  // Indian number / currency / date
  formatIndianNumber,
  formatRupees,
  formatRupeesCompact,
  formatIndianDate,
} from '@garh/model';

export type { UnitsDisplay, FtInOptions } from '@garh/model';

import { formatFtIn, formatLength, roundMm, type UnitsDisplay } from '@garh/model';

import { DEFAULT_UNITS_DISPLAY } from './env';

export { DEFAULT_UNITS_DISPLAY };

// ---------------------------------------------------------------------------
// Snap modules (§F4 "snap default = 115mm half-brick module (4.5in)")
// ---------------------------------------------------------------------------

/**
 * Default snap: the half-brick module. Everything the solver emits is already
 * on this grid (§5.3 stage B), so drawing by hand lands on the same lines the
 * generated plans do.
 */
export const SNAP_COARSE_MM = 115;

/**
 * Fine grid, for the cases the module cannot express — a 2400mm corridor that
 * has to clear a 2390mm obstruction, say.
 *
 * ASSUMPTION: the playbook specifies the 115mm default and says there is a
 * "fine-grid toggle" without naming its value. 25mm is a round number in both
 * systems (≈1in) and is small enough to be a genuine escape hatch without
 * being pixel-level. Change it here, not at call sites.
 */
export const SNAP_FINE_MM = 25;

/** Round a length onto a grid. Returns integer mm; never a float. */
export function snapMm(valueMm: number, moduleMm: number): number {
  if (moduleMm <= 0) return roundMm(valueMm);
  return roundMm(valueMm / moduleMm) * moduleMm;
}

// ---------------------------------------------------------------------------
// Display helpers the UI reaches for
// ---------------------------------------------------------------------------

/**
 * A length in the project's units, in the form drawings use: `12'-6"` or
 * `3.81 m`. The default is ft-in because that is what Indian residential
 * practice quotes (§15).
 */
export function formatLengthDisplay(
  mm: number,
  display: UnitsDisplay = DEFAULT_UNITS_DISPLAY,
): string {
  return formatLength(mm, display);
}

/** `30'-0" x 40'-0"` — plot and room dimension pairs. */
export function formatDimensionPair(
  widthMm: number,
  depthMm: number,
  display: UnitsDisplay = DEFAULT_UNITS_DISPLAY,
): string {
  const w = display === 'ft-in' ? formatFtIn(widthMm, { dropZeroInches: true }) : formatLength(widthMm, display);
  const d = display === 'ft-in' ? formatFtIn(depthMm, { dropZeroInches: true }) : formatLength(depthMm, display);
  return `${w} × ${d}`;
}

/**
 * `+91 98765 43210`. Accepts anything the user typed; falls back to returning
 * the input untouched rather than mangling a number we do not recognise.
 */
export function formatPhoneIn(raw: string): string {
  const digits = raw.replace(/\D/g, '');
  const local = digits.length === 12 && digits.startsWith('91') ? digits.slice(2) : digits;
  if (local.length !== 10) return raw.trim();
  return `+91 ${local.slice(0, 5)} ${local.slice(5)}`;
}

/**
 * DD-MM-YYYY (§15). Empty string for a null/absent timestamp — never
 * "Invalid Date".
 *
 * NOTE: this deliberately does NOT delegate to `@garh/model`'s
 * `formatIndianDate`, which formats in **UTC**. That is the right choice where
 * it is used — a drawing's title-block date must not depend on the reader's
 * machine — but it is the wrong one in the app: a project edited at 11pm IST
 * would show yesterday's date, and, worse, would disagree with
 * {@link formatDateTime} on the same instant, which reads local components.
 * Screens are local; sheets are UTC. `formatIndianDate` stays exported for the
 * sheet layer.
 */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return '';
  const date = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return '';
  const dd = String(date.getDate()).padStart(2, '0');
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  return `${dd}-${mm}-${date.getFullYear()}`;
}

/** `05-08-2026, 14:32` — local time, 24-hour, for job and version timelines. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return '';
  const date = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return '';
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const mon = String(date.getMonth() + 1).padStart(2, '0');
  return `${dd}-${mon}-${date.getFullYear()}, ${hh}:${mm}`;
}

/**
 * "just now" / "4 min ago" / "yesterday" / a date. Used by the autosave badge
 * and the version timeline, where an exact timestamp is noise.
 */
export function formatRelative(value: string | Date | null | undefined, now = Date.now()): string {
  if (!value) return '';
  const date = typeof value === 'string' ? new Date(value) : value;
  const ms = date.getTime();
  if (Number.isNaN(ms)) return '';
  const seconds = Math.floor((now - ms) / 1000);
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return formatDate(date);
}

/**
 * The autosave badge's version label: `Saved · v214` (§15).
 *
 * `v` is the op count on the branch — `headIdx + 1`, because the log is
 * 0-indexed and `-1` means "nothing appended yet". A freshly created project
 * therefore reads `v0`, which is true and not a lie about a version that
 * does not exist.
 */
export function formatVersionLabel(headIdx: number): string {
  return `v${Math.max(0, headIdx + 1)}`;
}
