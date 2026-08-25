/**
 * units.ts — the integer-millimetre boundary.
 *
 * GOLDEN RULE 6 ("mm in, pretty out"): every user-supplied length is parsed to
 * integer millimetres here, and every displayed length is formatted here. No
 * other module in the product is allowed to know about feet, inches, gaj or ₹.
 *
 * ROUNDING POLICY (documented because it is a cross-language contract):
 *   All mm rounding is ROUND-HALF-AWAY-FROM-ZERO ("commercial rounding"):
 *       0.5 -> 1,  1.5 -> 2,  2.5 -> 3,  -0.5 -> -1,  -2.5 -> -3
 *   Banker's / half-to-even rounding is explicitly NOT wanted — architects
 *   expect 2.5mm to become 3mm, and a dimension chain that rounds half-to-even
 *   sums differently depending on the parity of its parts.
 *   JavaScript `Math.round` is round-half-UP (i.e. -0.5 -> -0), which is wrong
 *   for negatives, and Python's builtin `round` is half-to-even, which is wrong
 *   everywhere. Both languages must implement exactly:
 *       x >= 0 ? floor(x + 0.5) : -floor(-x + 0.5)
 *   using IEEE-754 doubles, which makes TS and Python agree bit-for-bit.
 *
 * EXACTNESS: imperial conversion factors are exact decimals (1in = 25.4mm), so
 * the only inexactness is the final rounding to whole mm, which is the point.
 */

/** Exact: 1 inch = 25.4 mm. */
export const MM_PER_INCH = 25.4;
/** Exact: 1 foot = 304.8 mm. */
export const MM_PER_FOOT = 304.8;
/** Exact: 1 yard = 914.4 mm. */
export const MM_PER_YARD = 914.4;
/** Exact: 1 metre = 1000 mm. */
export const MM_PER_METRE = 1000;
/** Exact: 1 centimetre = 10 mm. */
export const MM_PER_CM = 10;
/** Exact: 1 sq ft = 92_903.04 mm² (304.8²). */
export const MM2_PER_SQFT = MM_PER_FOOT * MM_PER_FOOT;
/** Exact: 1 sq m = 1_000_000 mm². */
export const MM2_PER_SQM = 1_000_000;
/** 1 gaj = 1 square yard = 9 sq ft = 836_127.36 mm². */
export const MM2_PER_GAJ = MM_PER_YARD * MM_PER_YARD;

/** Display unit system for a project. Mirrors `projects.units` in the DB. */
export type UnitsDisplay = 'ft-in' | 'm';

/** Thrown by {@link parseLengthMm} when the input cannot be understood. */
export class UnitParseError extends Error {
  readonly code = 'UNIT_PARSE_FAILED';
  readonly input: string;
  constructor(input: string, reason: string) {
    super(`Cannot read "${input}" as a length: ${reason}`);
    this.name = 'UnitParseError';
    this.input = input;
  }
}

/**
 * Round-half-away-from-zero. THE only rounding function allowed on lengths.
 * Python mirror: `x if x >= 0 else -...` with `math.floor(x + 0.5)`.
 */
export function roundHalfAwayFromZero(x: number): number {
  if (!Number.isFinite(x)) {
    throw new RangeError(`roundHalfAwayFromZero: not a finite number (${String(x)})`);
  }
  // `+ 0` normalises the negative branch's -0 to +0: Python's int 0 has no
  // sign, so a -0 here would be a mirror divergence `Object.is` can see.
  return x >= 0 ? Math.floor(x + 0.5) : -Math.floor(-x + 0.5) + 0;
}

/** Alias used at call sites where "this value becomes integer mm" is the point. */
export const roundMm = roundHalfAwayFromZero;

/**
 * Apply a parsed sign to a rounded magnitude. The `+ 0` folds `-1 * 0` back
 * to unsigned zero — "-0.4mm" must parse to the same 0 the Python mirror
 * returns, and `Object.is` in the golden-pair spec can see the difference.
 */
function applySign(sign: number, mm: number): number {
  return sign * mm + 0;
}

/** True when `v` is a value we are willing to store as a length/coordinate. */
export function isIntMm(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v);
}

/** Assert integer mm, with a message naming the field (used by validate.ts). */
export function assertIntMm(v: unknown, field: string): number {
  if (!isIntMm(v)) {
    throw new RangeError(`${field} must be an integer number of millimetres, got ${String(v)}`);
  }
  return v;
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

/**
 * Normalise the many ways an Indian architect types a length.
 *  - unicode primes/quotes -> ASCII ' and "
 *  - unicode spaces (NBSP, thin, narrow-NBSP) -> ASCII space
 *  - unicode minus/en-dash used as a minus or as a feet-inch separator -> '-'
 *  - thousands commas between digits are dropped ("1,200" -> "1200")
 *  - collapsed whitespace, lower-cased
 */
export function normaliseLengthInput(raw: string): string {
  let s = raw
    // U+2032 PRIME, U+2019 RIGHT SINGLE QUOTE, U+02B9 MODIFIER PRIME, U+00B4 ACUTE, backtick
    .replace(/[\u2032\u2019\u02b9\u00b4`]/g, "'")
    // U+2033 DOUBLE PRIME, U+201D RIGHT DOUBLE QUOTE, U+02BA, U+3003, U+FF02
    .replace(/[\u2033\u201d\u02ba\u3003\uff02]/g, '"')
    // NBSP, en/em/thin spaces, narrow NBSP, medium math space, ideographic space
    .replace(/[\u00a0\u2000-\u200a\u202f\u205f\u3000]/g, ' ')
    // U+2212 MINUS SIGN, U+2013 EN DASH, U+2014 EM DASH
    .replace(/[\u2212\u2013\u2014]/g, '-')
    // Thousands separators: a comma preceded by a digit-or-comma and followed
    // by a digit. The comma in the left context is what turns "3,,800" into
    // "3,800" — still comma-carrying, so the parser rejects it as the golden
    // reject list requires — instead of leaving the double comma untouched.
    .replace(/(?<=[\d,]),(?=\d)/g, '');
  s = s.trim().replace(/\s+/g, ' ').toLowerCase();
  return s;
}

const UNIT_ALIASES: ReadonlyArray<readonly [RegExp, number]> = [
  [/^(?:mm|millimetre|millimetres|millimeter|millimeters)$/, 1],
  [/^(?:cm|centimetre|centimetres|centimeter|centimeters)$/, MM_PER_CM],
  [/^(?:m|mt|mtr|metre|metres|meter|meters)$/, MM_PER_METRE],
  [/^(?:ft|foot|feet)$/, MM_PER_FOOT],
  [/^(?:in|inch|inches)$/, MM_PER_INCH],
  [/^(?:yd|yard|yards)$/, MM_PER_YARD],
];

function unitFactor(token: string): number | null {
  for (const [re, factor] of UNIT_ALIASES) {
    if (re.test(token)) return factor;
  }
  return null;
}

/** `"6 1/2"` / `"6-1/2"` / `"1/2"` / `"6.5"` -> 6.5 (a plain decimal count). */
function parseMixedNumber(text: string): number | null {
  const s = text.trim();
  if (s === '') return null;
  let m = /^(\d+)\s*[-\s]\s*(\d+)\s*\/\s*(\d+)$/.exec(s);
  if (m) {
    const den = Number(m[3]);
    if (den === 0) return null;
    return Number(m[1]) + Number(m[2]) / den;
  }
  m = /^(\d+)\s*\/\s*(\d+)$/.exec(s);
  if (m) {
    const den = Number(m[2]);
    if (den === 0) return null;
    return Number(m[1]) / den;
  }
  if (/^\d+(?:\.\d+)?$/.test(s) || /^\.\d+$/.test(s)) return Number(s);
  return null;
}

/**
 * Parse ANY of the length forms an Indian architect actually types into
 * integer millimetres.
 *
 * Accepted (case-insensitive, whitespace-tolerant):
 *   bare number            "3800"            -> 3800   (bare == mm; see `defaultUnit`)
 *   explicit metric        "3800mm" "380cm" "3.8m" "3.8 metres"
 *   explicit imperial      "12ft" "12 feet" "150in" "150 inches" "4 yd"
 *   feet+inches            "12'6\"" "12' 6\"" "12'-6\"" "12'6" "12'"  "6\""
 *   dash shorthand         "12-6"            -> 12'-6"  (drafting shorthand)
 *   inch fractions         "6 1/2\"" "12'-6 1/2\"" "1/2\""
 *   decimals              "12.5'" "6.25in" "0.5mm"
 *   signed                 "-12'6\"" "-3800"
 *   unicode primes         "12′6″"
 *
 * @param raw       user text
 * @param defaultUnit unit assumed for a bare number. Default 'mm' — the model
 *                    is mm, so a bare number in an mm field means mm. UI code
 *                    that owns a ft-in field passes 'ft-in' so "12" means 12'.
 * @throws {UnitParseError}
 */
export function parseLengthMm(raw: string, defaultUnit: 'mm' | 'ft-in' | 'm' = 'mm'): number {
  if (typeof raw !== 'string') throw new UnitParseError(String(raw), 'not a string');
  const s0 = normaliseLengthInput(raw);
  if (s0 === '') throw new UnitParseError(raw, 'empty');

  let sign = 1;
  let s = s0;
  if (s.startsWith('-')) {
    sign = -1;
    s = s.slice(1).trim();
  } else if (s.startsWith('+')) {
    s = s.slice(1).trim();
  }
  if (s === '') throw new UnitParseError(raw, 'sign with no number');

  // --- 1. feet-and-inches with explicit marks: 12'6", 12' 6 1/2", 12', 6"
  const ftIn = /^(?:([0-9]+(?:\.[0-9]+)?)\s*')?\s*(?:[-\s]\s*)?(?:([0-9 /.]+?)\s*"?)?$/.exec(s);
  if (s.includes("'") && ftIn) {
    const feet = ftIn[1] === undefined ? 0 : Number(ftIn[1]);
    const inchText = (ftIn[2] ?? '').trim();
    const inches = inchText === '' ? 0 : parseMixedNumber(inchText);
    if (inches === null) throw new UnitParseError(raw, `cannot read inches part "${inchText}"`);
    return applySign(sign, roundMm((feet * 12 + inches) * MM_PER_INCH));
  }

  // --- 2. inches only with the " mark: 6", 6 1/2"
  if (s.endsWith('"')) {
    const inchText = s.slice(0, -1).trim();
    const inches = parseMixedNumber(inchText);
    if (inches === null) throw new UnitParseError(raw, `cannot read inches "${inchText}"`);
    return applySign(sign, roundMm(inches * MM_PER_INCH));
  }

  // --- 3. "12 ft 6 in" / "12 feet 6 inches"
  const ftInWords = /^([0-9.]+)\s*(?:ft|foot|feet)\s*([0-9 /.]+)?\s*(?:in|inch|inches)?$/.exec(s);
  if (ftInWords && ftInWords[2] !== undefined && ftInWords[2].trim() !== '') {
    const feet = Number(ftInWords[1]);
    const inches = parseMixedNumber(ftInWords[2]);
    if (inches === null) throw new UnitParseError(raw, `cannot read inches "${ftInWords[2]}"`);
    return applySign(sign, roundMm((feet * 12 + inches) * MM_PER_INCH));
  }

  // --- 4. number + unit word: 3.8m, 3800mm, 12 ft, 4 yd, 150 in
  const withUnit = /^([0-9]*\.?[0-9]+(?:\s+[0-9]+\/[0-9]+)?)\s*([a-z]+)$/.exec(s);
  if (withUnit) {
    const factor = unitFactor(withUnit[2]!);
    if (factor === null) throw new UnitParseError(raw, `unknown unit "${withUnit[2]}"`);
    const n = parseMixedNumber(withUnit[1]!);
    if (n === null) throw new UnitParseError(raw, `cannot read number "${withUnit[1]}"`);
    return applySign(sign, roundMm(n * factor));
  }

  // --- 5. dash shorthand "12-6" == 12'-6" (integers only, no unit marks)
  const dash = /^([0-9]+)\s*-\s*([0-9]+(?:\s+[0-9]+\/[0-9]+)?|[0-9]+\/[0-9]+)$/.exec(s);
  if (dash) {
    const feet = Number(dash[1]);
    const inches = parseMixedNumber(dash[2]!);
    if (inches === null) throw new UnitParseError(raw, `cannot read inches "${dash[2]}"`);
    return applySign(sign, roundMm((feet * 12 + inches) * MM_PER_INCH));
  }

  // --- 6. bare number / bare fraction -> defaultUnit
  const bare = parseMixedNumber(s);
  if (bare !== null) {
    if (defaultUnit === 'mm') return applySign(sign, roundMm(bare));
    if (defaultUnit === 'm') return applySign(sign, roundMm(bare * MM_PER_METRE));
    return applySign(sign, roundMm(bare * MM_PER_FOOT)); // 'ft-in': a bare number is feet
  }

  throw new UnitParseError(raw, 'unrecognised format');
}

/** Non-throwing variant for form fields. */
export function tryParseLengthMm(
  raw: string,
  defaultUnit: 'mm' | 'ft-in' | 'm' = 'mm',
): { ok: true; mm: number } | { ok: false; error: string } {
  try {
    return { ok: true, mm: parseLengthMm(raw, defaultUnit) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/**
 * Parse an area. Accepts "1200 sqft", "1,200 sq ft", "133 gaj", "111 sqm",
 * "111 m2", "30x40 ft" (a rectangle!), bare number -> sq ft by default.
 * Returns integer mm².
 */
export function parseAreaMm2(raw: string, defaultUnit: 'sqft' | 'sqm' | 'gaj' = 'sqft'): number {
  const s = normaliseLengthInput(raw);
  if (s === '') throw new UnitParseError(raw, 'empty');

  // "30x40" / "30 x 40 ft" — a plot stated as a rectangle.
  const rect = /^([0-9.]+)\s*[x×*]\s*([0-9.]+)\s*([a-z]+)?$/.exec(s);
  if (rect) {
    const unit = rect[3] ?? (defaultUnit === 'sqm' ? 'm' : 'ft');
    const factor = unitFactor(unit);
    if (factor === null) throw new UnitParseError(raw, `unknown unit "${unit}"`);
    const a = roundMm(Number(rect[1]) * factor);
    const b = roundMm(Number(rect[2]) * factor);
    return a * b;
  }

  const m = /^([0-9]*\.?[0-9]+)\s*(.*)$/.exec(s);
  if (!m) throw new UnitParseError(raw, 'unrecognised area');
  const n = Number(m[1]);
  const unit = (m[2] ?? '').replace(/[\s.]/g, '');
  const key = unit === '' ? defaultUnit : unit;
  switch (key) {
    case 'sqft':
    case 'sqfeet':
    case 'ft2':
    case 'sft':
    case 'squarefeet':
    case 'squarefoot':
      return roundMm(n * MM2_PER_SQFT);
    case 'sqm':
    case 'm2':
    case 'sqmt':
    case 'squaremetre':
    case 'squaremetres':
    case 'squaremeter':
    case 'squaremeters':
      return roundMm(n * MM2_PER_SQM);
    case 'gaj':
    case 'sqyd':
    case 'yd2':
    case 'squareyard':
    case 'squareyards':
      return roundMm(n * MM2_PER_GAJ);
    default:
      throw new UnitParseError(raw, `unknown area unit "${unit}"`);
  }
}

// ---------------------------------------------------------------------------
// Formatting — lengths
// ---------------------------------------------------------------------------

/** Options for {@link formatFtIn}. */
export interface FtInOptions {
  /** Inch fraction resolution: 1 = whole inches (default), 2 = ½, 4 = ¼, 8 = ⅛. */
  fraction?: 1 | 2 | 4 | 8;
  /** Include the trailing `"` on inches. Default true. */
  inchMark?: boolean;
  /** Omit `0"` when the length is a whole number of feet. Default false — municipal drawings want 12'-0". */
  dropZeroInches?: boolean;
}

const FRACTION_GLYPHS: Record<string, string> = {
  '1/2': '½',
  '1/4': '¼',
  '3/4': '¾',
  '1/8': '⅛',
  '3/8': '⅜',
  '5/8': '⅝',
  '7/8': '⅞',
};

/** 3810 -> `12'-6"`. Negative lengths keep the sign outside: `-12'-6"`. */
export function formatFtIn(mm: number, opts: FtInOptions = {}): string {
  assertIntMm(mm, 'mm');
  const fraction = opts.fraction ?? 1;
  const inchMark = opts.inchMark ?? true;
  const sign = mm < 0 ? '-' : '';
  const abs = Math.abs(mm);

  // work in fraction-units of an inch to keep the carry logic integral
  const units = roundHalfAwayFromZero((abs / MM_PER_INCH) * fraction);
  const unitsPerFoot = 12 * fraction;
  let feet = Math.floor(units / unitsPerFoot);
  let rem = units - feet * unitsPerFoot;
  let inches = Math.floor(rem / fraction);
  const num = rem - inches * fraction;

  if (inches === 12) {
    feet += 1;
    inches = 0;
    rem = 0;
  }

  let inchText = String(inches);
  if (num > 0) {
    const g = gcd(num, fraction);
    const key = `${num / g}/${fraction / g}`;
    const glyph = FRACTION_GLYPHS[key] ?? key;
    inchText = inches === 0 ? glyph : `${inches}${glyph}`;
  }

  if (opts.dropZeroInches && inches === 0 && num === 0) {
    return `${sign}${feet}'`;
  }
  return `${sign}${feet}'-${inchText}${inchMark ? '"' : ''}`;
}

function gcd(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);
  while (y !== 0) {
    const t = x % y;
    x = y;
    y = t;
  }
  return x === 0 ? 1 : x;
}

/** 3800 -> `3.80 m`. `decimals` default 2, `unit` suffix can be dropped. */
export function formatMetres(mm: number, decimals = 2, withUnit = true): string {
  assertIntMm(mm, 'mm');
  const sign = mm < 0 ? '-' : '';
  const abs = Math.abs(mm);
  const scale = Math.pow(10, decimals);
  const scaled = roundHalfAwayFromZero((abs * scale) / MM_PER_METRE);
  const whole = Math.floor(scaled / scale);
  const frac = scaled - whole * scale;
  const body = decimals === 0 ? String(whole) : `${whole}.${String(frac).padStart(decimals, '0')}`;
  return `${sign}${body}${withUnit ? ' m' : ''}`;
}

/** 3800 -> `3800 mm`. Drawings always dim in mm (playbook §7). */
export function formatMm(mm: number, withUnit = true): string {
  assertIntMm(mm, 'mm');
  return withUnit ? `${mm} mm` : String(mm);
}

/** Format per project display units. */
export function formatLength(mm: number, display: UnitsDisplay, opts: FtInOptions = {}): string {
  return display === 'ft-in' ? formatFtIn(mm, opts) : formatMetres(mm);
}

// ---------------------------------------------------------------------------
// Formatting — areas
// ---------------------------------------------------------------------------

/** mm² -> sq ft (float; DISPLAY ONLY — never feed this back into geometry). */
export function toSqft(mm2: number): number {
  return mm2 / MM2_PER_SQFT;
}
/** mm² -> sq m (float; display only). */
export function toSqm(mm2: number): number {
  return mm2 / MM2_PER_SQM;
}
/** mm² -> gaj (= sq yard = 9 sq ft) (float; display only). */
export function toGaj(mm2: number): number {
  return mm2 / MM2_PER_GAJ;
}
/** sq ft -> mm² (integer). */
export function fromSqft(sqft: number): number {
  return roundMm(sqft * MM2_PER_SQFT);
}
/** sq m -> mm² (integer). */
export function fromSqm(sqm: number): number {
  return roundMm(sqm * MM2_PER_SQM);
}
/** gaj -> mm² (integer). */
export function fromGaj(gaj: number): number {
  return roundMm(gaj * MM2_PER_GAJ);
}

/** Fixed-decimal formatting with round-half-away-from-zero (never toFixed). */
export function formatFixed(value: number, decimals: number): string {
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  const scale = Math.pow(10, decimals);
  const scaled = roundHalfAwayFromZero(abs * scale);
  const whole = Math.floor(scaled / scale);
  const frac = scaled - whole * scale;
  if (decimals === 0) return `${sign}${formatIndianNumber(whole)}`;
  return `${sign}${formatIndianNumber(whole)}.${String(frac).padStart(decimals, '0')}`;
}

/** `1,200.5 sq ft` — one decimal is the municipal-drawing convention (§7). */
export function formatSqft(mm2: number, decimals = 1): string {
  return `${formatFixed(toSqft(mm2), decimals)} sq ft`;
}
/** `111.48 m²` */
export function formatSqm(mm2: number, decimals = 2): string {
  return `${formatFixed(toSqm(mm2), decimals)} m²`;
}
/** `133 gaj` — plot sizes in north India are quoted in gaj. */
export function formatGaj(mm2: number, decimals = 0): string {
  return `${formatFixed(toGaj(mm2), decimals)} gaj`;
}
/** Area per project display units. */
export function formatArea(mm2: number, display: UnitsDisplay): string {
  return display === 'ft-in' ? formatSqft(mm2) : formatSqm(mm2);
}
/** `1,200.0 sq ft · 133 gaj` — the plot-header string from §15. */
export function formatPlotArea(mm2: number, display: UnitsDisplay = 'ft-in'): string {
  return `${formatArea(mm2, display)} · ${formatGaj(mm2)}`;
}

// ---------------------------------------------------------------------------
// Indian number / currency formatting
// ---------------------------------------------------------------------------

/**
 * Indian digit grouping (lakh/crore): last 3 digits, then groups of 2.
 * 1245000 -> "12,45,000"; 999 -> "999"; -1234567 -> "-12,34,567".
 */
export function formatIndianNumber(n: number): string {
  if (!Number.isFinite(n)) throw new RangeError(`formatIndianNumber: ${String(n)}`);
  const neg = n < 0;
  const whole = Math.floor(Math.abs(n));
  const digits = String(whole);
  let out: string;
  if (digits.length <= 3) {
    out = digits;
  } else {
    const head = digits.slice(0, digits.length - 3);
    const tail = digits.slice(digits.length - 3);
    const groups: string[] = [];
    let i = head.length;
    while (i > 2) {
      groups.unshift(head.slice(i - 2, i));
      i -= 2;
    }
    if (i > 0) groups.unshift(head.slice(0, i));
    out = `${groups.join(',')},${tail}`;
  }
  return neg ? `-${out}` : out;
}

/** `₹12,45,000`. Rupees only (no paise) — this is a budget field, not a ledger. */
export function formatRupees(rupees: number, opts: { decimals?: 0 | 2 } = {}): string {
  const decimals = opts.decimals ?? 0;
  const neg = rupees < 0;
  const body = formatFixed(Math.abs(rupees), decimals);
  return `${neg ? '-' : ''}₹${body}`;
}

/** `₹1.25 Cr` / `₹45.0 L` / `₹85,000` — compact budget bands for chips. */
export function formatRupeesCompact(rupees: number): string {
  const abs = Math.abs(rupees);
  const sign = rupees < 0 ? '-' : '';
  if (abs >= 10_000_000) return `${sign}₹${formatFixed(abs / 10_000_000, 2)} Cr`;
  if (abs >= 100_000) return `${sign}₹${formatFixed(abs / 100_000, 1)} L`;
  return formatRupees(rupees);
}

/** DD-MM-YYYY (§15 Indian defaults). Takes an ISO date or a Date. */
export function formatIndianDate(d: Date | string): string {
  const date = typeof d === 'string' ? new Date(d) : d;
  const dd = String(date.getUTCDate()).padStart(2, '0');
  const mm = String(date.getUTCMonth() + 1).padStart(2, '0');
  return `${dd}-${mm}-${date.getUTCFullYear()}`;
}

// ---------------------------------------------------------------------------
// GOLDEN_UNIT_PAIRS — cross-language contract
// ---------------------------------------------------------------------------

/**
 * CROSS-LANGUAGE CONTRACT.
 *
 * `apps/api/garh_model/units.py` MUST assert this exact table (it is exported to
 * `schema/golden-unit-pairs.json` for that purpose). If TS and Python disagree
 * on one row, dimension chains and compliance numbers disagree between the
 * canvas and the drawing set — so a mismatch is a build failure, never a
 * "close enough".
 *
 * Each row is `[input, expectedMm]` parsed with `defaultUnit: 'mm'`.
 */
export const GOLDEN_UNIT_PAIRS: ReadonlyArray<readonly [string, number]> = [
  // --- zero and identity
  ['0', 0],
  ['0mm', 0],
  ['0.0m', 0],
  ['0\'0"', 0],
  ['3800', 3800],
  ['3800mm', 3800],
  ['3800 mm', 3800],

  // --- rounding policy: half AWAY FROM ZERO (banker's would give 2 / 2 / -2)
  ['0.5mm', 1],
  ['1.5mm', 2],
  ['2.5mm', 3],
  ['-0.5mm', -1],
  ['-2.5mm', -3],
  ['0.4mm', 0],
  ['-0.4mm', 0],

  // --- metric
  ['3.8m', 3800],
  ['3.8 m', 3800],
  ['3.8metres', 3800],
  ['3.8 meters', 3800],
  ['380cm', 3800],
  ['1m', 1000],
  ['12.345m', 12345],
  ['0.001m', 1],

  // --- imperial whole units (note 12ft = 3657.6 -> 3658, NOT 3657)
  ['12ft', 3658],
  ['12 ft', 3658],
  ['12 feet', 3658],
  ["12'", 3658],
  ['1ft', 305],
  ["1'", 305],
  ['1in', 25],
  ['1 inch', 25],
  ['9in', 229],
  ['9"', 229],
  ['4.5in', 114],
  ['1yd', 914],
  ['1 yard', 914],

  // --- feet + inches, every separator an architect uses
  ['12\'6"', 3810],
  ["12'6", 3810],
  ['12\' 6"', 3810],
  ['12\'-6"', 3810],
  ['12 ft 6 in', 3810],
  ['12 feet 6 inches', 3810],
  ['12-6', 3810],
  ['30-0', 9144],
  ['40-0', 12192],

  // --- inch fractions
  ['6 1/2"', 165],
  ['6-1/2"', 165],
  ['1/2"', 13],
  ['12\'-6 1/2"', 3823],
  ['0-1/4', 6],

  // --- decimals in imperial
  ["12.5'", 3810],
  ['12.5 ft', 3810],
  ['6.25in', 159],

  // --- signs
  ['-3800', -3800],
  ['-3.8m', -3800],
  ['-12\'6"', -3810],
  ['-12-6', -3810],
  ['+3800', 3800],

  // --- whitespace and separators
  ['  3800  ', 3800],
  ['\t3800\n', 3800],
  ['1,200', 1200],
  ['12,45,000', 1245000],

  // --- unicode primes, quotes, dashes and spaces
  ['12\u203206\u2033', 3810], // 12'06" written with PRIME / DOUBLE PRIME
  ['12\u20326\u2033', 3810],
  ['12\u20196\u201d', 3810], // curly quotes from Word/WhatsApp
  ['12\u00a0ft', 3658], // NBSP between number and unit
  ['\u22123800', -3800], // U+2212 MINUS SIGN
  ['12\u20136', 3810], // en dash used as the feet-inch separator
];

/**
 * Inputs that MUST be rejected with `UnitParseError`. Python asserts this list
 * too: silently guessing a number from garbage is how a wall ends up 3m off.
 *
 * Note `'3 800'`: a space is NOT a thousands separator in this parser. Two
 * bare integers separated by a space is ambiguous ("3 feet 800?" "3800?"), so
 * we refuse it and let the UI show "Try 3800 or 3.8m".
 */
export const GOLDEN_UNIT_FAILURES: readonly string[] = [
  '',
  '   ',
  'abc',
  '3 800',
  '3\u00a0800 mm',
  '12 6',
  'twelve feet',
  '3.8 furlongs',
  '1/0"',
  '-',
  '--3800',
  '3,,800',
  '12\'6"7',
  '12ft6in3',
  'NaN',
  'Infinity',
];
