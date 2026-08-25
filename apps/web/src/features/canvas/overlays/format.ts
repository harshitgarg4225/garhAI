/**
 * format.ts — the overlay display boundary.
 *
 * Everything an overlay puts in front of a human — a dimension value, a room
 * area, the seed text of a click-to-edit field, the string it parses back —
 * goes through this module, and every conversion it performs is re-exported
 * from `lib/units` (which re-exports `@garh/model`). Golden rule 6: "mm in,
 * pretty out", once, in one place. There is no `/ 304.8` under `overlays/`.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE ONE DELIBERATE DIVERGENCE: A BARE NUMBER IN A DIMENSION BOX IS MILLIMETRES
 * ────────────────────────────────────────────────────────────────────────────
 * `LengthInput` (packages/ui) treats a bare number as the project's display
 * unit, because "12" typed into a wall-length field in a ft-in project means
 * twelve feet. A canvas dimension is different, and the difference is not a
 * preference:
 *
 *   · §7 states the drawing set dims in millimetres. The number an architect
 *     reads off the dimension string IS the millimetre value.
 *   · The value becomes an op payload directly, and op payloads are mm.
 *   · Every worked example of this interaction types `3600`.
 *
 * So {@link DIMENSION_BARE_UNIT} is `'mm'`, unconditionally, and the edit field
 * says so in its hint. `12'6"`, `3.8m`, `380cm`, `12-6` all still parse exactly
 * as they do everywhere else — only the meaning of an unqualified integer is
 * pinned. See `DECISIONS.md` note in the phase-4 return summary.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * AREA DECIMALS
 * ────────────────────────────────────────────────────────────────────────────
 * One decimal in both systems: `153.4 sq ft`, `14.2 m²`. §7 fixes sq ft at one
 * decimal for the drawing set, and the canvas tag must not disagree with the
 * sheet it will become. Two decimals of square metres on a room tag is noise
 * you cannot read at 1:100 anyway.
 */

import {
  formatFixed,
  formatFtIn,
  formatIndianNumber,
  formatMetres,
  parseAreaMm2,
  toSqft,
  toSqm,
  tryParseLengthMm,
  type UnitsDisplay,
} from '../../../lib/units';

// ---------------------------------------------------------------------------
// Lengths — dimension strings
// ---------------------------------------------------------------------------

/** What an unqualified number means in a dimension edit box. See module docs. */
export const DIMENSION_BARE_UNIT = 'mm' as const;

/**
 * The label printed on a dimension segment, in the project's display units.
 *
 * `dropZeroInches` is off: municipal practice writes `12'-0"`, not `12'`, and
 * the canvas showing one form while the sheet shows the other is the kind of
 * inconsistency that gets a drawing bounced.
 */
export function dimensionText(mm: number, display: UnitsDisplay): string {
  return display === 'ft-in' ? formatFtIn(mm, { fraction: 8 }) : formatMetres(mm, 3);
}

/**
 * The same value in millimetres, which is what the sheet engine prints (§7).
 * Shown as the secondary line while a dimension is being edited so the number
 * that will land in the op is never hidden.
 */
export function dimensionTextMm(mm: number): string {
  return `${formatIndianNumber(mm)} mm`;
}

/**
 * Seed text for a click-to-edit dimension field: the plain millimetre integer.
 *
 * Not the formatted `12'-6"`. The field's bare unit is mm, so seeding it with
 * its own canonical form means select-all-and-retype produces the same value it
 * displayed — a field that round-trips through itself. Seeding `12'-6"` would
 * work too (it parses), but it makes the common case — nudging 3600 to 3650 —
 * require deleting a quote mark.
 */
export function dimensionEditSeed(mm: number): string {
  return String(mm);
}

export type ParseResult =
  | { readonly ok: true; readonly mm: number }
  | { readonly ok: false; readonly error: string };

/** Formats that always work, echoed when a parse fails. Kept short. */
const DIMENSION_EXAMPLES = `3600, 12'6", 3.6m or 12-6`;

/**
 * Vulgar-fraction glyphs → the ASCII form `parseLengthMm` reads.
 *
 * WHY THIS EXISTS. `formatFtIn(mm, { fraction: 8 })` prints `12'-6½"` using
 * typographic glyphs, and `parseLengthMm` does not accept them — so the app's
 * own ⅛-inch output does not survive a round trip through its own parser. Since
 * a click-to-edit field seeds itself with formatted text, that gap is the
 * difference between "open a dimension, press Enter, nothing happens" and "open
 * a dimension, press Enter, get an error on a value you did not change".
 *
 * Folded here rather than in `@garh/model` on purpose: `units.ts` is the
 * cross-language contract asserted byte-for-byte against `units.py`
 * (`GOLDEN_UNIT_PAIRS`), and widening its accepted grammar means changing both
 * sides and the golden table. This is the display layer's problem, so the
 * display layer solves it. The leading space matters: `10⅛` has to become
 * `10 1/8`, which is the mixed-number form the parser understands.
 */
const FRACTION_GLYPHS: ReadonlyArray<readonly [RegExp, string]> = [
  [/½/g, ' 1/2'],
  [/¼/g, ' 1/4'],
  [/¾/g, ' 3/4'],
  [/⅛/g, ' 1/8'],
  [/⅜/g, ' 3/8'],
  [/⅝/g, ' 5/8'],
  [/⅞/g, ' 7/8'],
  [/⅓/g, ' 1/3'],
  [/⅔/g, ' 2/3'],
];

/** Expand fraction glyphs. Exported for the spec; harmless on plain input. */
export function expandFractionGlyphs(raw: string): string {
  let out = raw;
  for (const [re, ascii] of FRACTION_GLYPHS) out = out.replace(re, ascii);
  return out;
}

/**
 * Parse what someone typed into a dimension box.
 *
 * Rejects zero and negatives here rather than letting `fold` reject them later:
 * "a wall cannot be 0 mm long" is a better sentence from the field the number
 * was typed into than from a toast three hundred milliseconds afterwards.
 */
export function parseDimensionInput(raw: string): ParseResult {
  const parsed = tryParseLengthMm(expandFractionGlyphs(raw), DIMENSION_BARE_UNIT);
  if (!parsed.ok) return { ok: false, error: `We couldn't read that. Try ${DIMENSION_EXAMPLES}.` };
  if (parsed.mm <= 0) return { ok: false, error: 'A dimension has to be greater than zero.' };
  return { ok: true, mm: parsed.mm };
}

// ---------------------------------------------------------------------------
// Areas — room tags
// ---------------------------------------------------------------------------

/** Decimals on a room-tag area. One, in both systems — see module docs. */
export const AREA_DECIMALS = 1;

/** `153.4 sq ft` / `14.2 m²`, per project units. */
export function roomAreaText(mm2: number, display: UnitsDisplay): string {
  return display === 'ft-in'
    ? `${formatFixed(toSqft(mm2), AREA_DECIMALS)} sq ft`
    : `${formatFixed(toSqm(mm2), AREA_DECIMALS)} m²`;
}

/** Seed text for the click-to-edit area field: the number without its unit. */
export function areaEditSeed(mm2: number, display: UnitsDisplay): string {
  return formatFixed(display === 'ft-in' ? toSqft(mm2) : toSqm(mm2), AREA_DECIMALS);
}

export type AreaParseResult =
  | { readonly ok: true; readonly mm2: number }
  | { readonly ok: false; readonly error: string };

/**
 * Parse a target-area input. A bare number here IS the display unit — unlike a
 * length, an area has no millimetre convention anyone types (nobody writes a
 * bedroom as 13 000 000 mm²), and `parseAreaMm2` already understands `sq ft`,
 * `sqm`, `m2`, `gaj` and `12x14` explicitly.
 */
export function parseAreaInput(raw: string, display: UnitsDisplay): AreaParseResult {
  try {
    // `formatSqm` writes `m²`, and `parseAreaMm2` knows `m2` — so the app's own
    // output would not survive a round trip through its own parser. Folding the
    // superscript here rather than widening the model's unit table keeps the
    // cross-language golden table (TS ↔ Python) exactly as it is.
    const mm2 = parseAreaMm2(raw.replace(/[²]/g, '2'), display === 'ft-in' ? 'sqft' : 'sqm');
    if (mm2 <= 0) return { ok: false, error: 'An area has to be greater than zero.' };
    return { ok: true, mm2 };
  } catch {
    // `parseAreaMm2` throws `UnitParseError` with a developer-facing reason
    // ("unknown area unit \"furlongs\""). The field says what DOES work
    // instead — golden rule 9: an error names the next action, not the fault.
    return { ok: false, error: `We couldn't read that. Try 150, 150 sq ft, 14 m² or 12x14.` };
  }
}

// ---------------------------------------------------------------------------
// Hints
// ---------------------------------------------------------------------------

/** The one-line hint under a dimension edit field. States the bare-unit rule. */
export function dimensionHint(display: UnitsDisplay): string {
  return display === 'ft-in'
    ? `A plain number is millimetres. 12'6" and 3.6m work too.`
    : `A plain number is millimetres. 3.6m and 12'6" work too.`;
}

/** The hint under a room-area edit field. */
export function areaHint(display: UnitsDisplay): string {
  return display === 'ft-in'
    ? 'A plain number is square feet. Try 150, 14 m² or 12x14.'
    : 'A plain number is square metres. Try 14, 150 sq ft or 3.6x4.';
}
