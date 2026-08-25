/**
 * types.ts — the F2 brief-data contract, as the web client reads and writes it.
 *
 * `brief.data` is a free-form `JsonObject` in the model core (its shape is
 * "owned by the brief schema, not by geometry" — model.ts). THIS module is that
 * schema for the web app. Two hard constraints shape it:
 *
 *  1. **Field names are shared with the LLM brief parser.** `services/llm/
 *     schemas.py` emits `storeys`, `hasStilt`, `hasBasement`, `rooms[]`
 *     (`{type, count, notes, targetAreaMm2}`), `budgetInr`, `parkingCount`,
 *     `familySize`, `adjacencies`, `notes`. The form fields the parser does not
 *     know about (kitchenType, style, rate, Vastu prefs…) are additive keys the
 *     parser simply never touches — so a parse merge-patched over a typed brief
 *     can never clobber them.
 *
 *  2. **Reads are tolerant, writes are canonical.** `brief.data` may contain
 *     anything (an old client, a parser fixture, a hand-edited seed), so
 *     `readBriefData` type-checks every field and drops what it cannot read
 *     instead of crashing the tab. Everything the form writes back goes through
 *     `normaliseRooms` / the typed helpers, so the stored shape converges.
 *
 * Geometry rule: the only length-ish field here is `targetAreaMm2`, an INTEGER
 * mm² parsed at the boundary (`parseAreaMm2`). No floats, ever.
 */

import {
  DIRECTIONS_8,
  ROOM_TYPES,
  ROOM_TYPE_LABELS,
  fromSqft,
  roundHalfAwayFromZero,
  type Direction8,
  type JsonObject,
  type JsonValue,
  type RoomType,
} from '@garh/model';

// ---------------------------------------------------------------------------
// Enumerations the form owns
// ---------------------------------------------------------------------------

export const KITCHEN_TYPES = ['open', 'semi-open', 'closed'] as const;
export type KitchenType = (typeof KITCHEN_TYPES)[number];

export const KITCHEN_TYPE_LABELS: Readonly<Record<KitchenType, string>> = {
  open: 'Open (part of living/dining)',
  'semi-open': 'Semi-open (breakfast counter)',
  closed: 'Closed (separate room)',
};

export const LIVING_DINING_CHOICES = ['combined', 'separate'] as const;
export type LivingDining = (typeof LIVING_DINING_CHOICES)[number];

export const BATH_CHOICES = ['attached', 'common'] as const;
export type BathChoice = (typeof BATH_CHOICES)[number];

/** The two launch facade kits — locked decision D3, never a free-text style. */
export const STYLE_KITS = [
  {
    id: 'contemporary',
    name: 'Contemporary',
    blurb: 'Flat chajjas, a vertical cladding band at the stair, slim MS railings. Monochrome with a wood accent.',
  },
  {
    id: 'modern-minimal',
    name: 'Modern Minimal',
    blurb: 'Recessed windows with hidden chajjas, plain parapet, glass railing. White and grey.',
  },
] as const;
export type StyleKitId = (typeof STYLE_KITS)[number]['id'];

/**
 * Budget bands (§F2). `midInr` is the value written to `budgetInr` when the
 * band is picked without an exact figure — visible and editable, per golden
 * rule 4, never a hidden midpoint.
 */
export const BUDGET_BANDS = [
  { id: 'under-25l', label: 'Under ₹25 L', midInr: 2_000_000 },
  { id: '25l-50l', label: '₹25 L – ₹50 L', midInr: 3_750_000 },
  { id: '50l-1cr', label: '₹50 L – ₹1 Cr', midInr: 7_500_000 },
  { id: '1cr-2cr', label: '₹1 Cr – ₹2 Cr', midInr: 15_000_000 },
  { id: 'over-2cr', label: 'Over ₹2 Cr', midInr: 25_000_000 },
] as const;
export type BudgetBandId = (typeof BUDGET_BANDS)[number]['id'];

/**
 * The default construction rate the area target derives from. An ASSUMPTION,
 * always rendered as an editable chip with this reason attached (golden rule 4).
 */
export const DEFAULT_RATE_PER_SQFT_INR = 1850;
export const RATE_ASSUMPTION_REASON =
  'Typical mid-range construction rate for Indian metros. Edit it to match your city and specification.';

// ---------------------------------------------------------------------------
// Vastu zone preferences (§F2 — "all editable")
// ---------------------------------------------------------------------------

export interface VastuPrefs {
  readonly entrance: readonly Direction8[];
  readonly pooja: readonly Direction8[];
  readonly kitchen: readonly Direction8[];
  readonly master: readonly Direction8[];
  readonly toilets: readonly Direction8[];
  /** Zones a toilet must NEVER occupy (hard rule even in advisory scoring). */
  readonly toiletsNever: readonly Direction8[];
  readonly stairs: readonly Direction8[];
  readonly tank: readonly Direction8[];
  /** Keep the centre cell (brahmasthan) free of enclosing walls. */
  readonly brahmasthanOpen: boolean;
}

/** A zone rule's key — every VastuPrefs field except the brahmasthan flag. */
export type VastuZoneKey = Exclude<keyof VastuPrefs, 'brahmasthanOpen'>;

/** The classical defaults, mirroring the Vastu rule pack (playbook §6). */
export const VASTU_DEFAULT_PREFS: VastuPrefs = {
  entrance: ['N', 'NE', 'E'],
  pooja: ['NE'],
  kitchen: ['SE', 'NW'],
  master: ['SW'],
  toilets: ['W', 'NW'],
  toiletsNever: ['NE'],
  stairs: ['S', 'SW', 'W'],
  tank: ['NE'],
  brahmasthanOpen: true,
};

/** Zone rules in display order, with the copy the selector renders. */
export const VASTU_ZONE_RULES: ReadonlyArray<{
  key: VastuZoneKey;
  label: string;
  hint: string;
}> = [
  { key: 'entrance', label: 'Main entrance', hint: 'Which sides the entry door may face.' },
  { key: 'pooja', label: 'Pooja', hint: 'Preferred corner for the pooja room or niche.' },
  { key: 'kitchen', label: 'Kitchen', hint: 'South-east is classical; north-west scores half.' },
  { key: 'master', label: 'Master bedroom', hint: 'Preferred corner for the master bedroom.' },
  { key: 'toilets', label: 'Toilets', hint: 'Preferred zones for baths and WCs.' },
  { key: 'toiletsNever', label: 'Toilets — never', hint: 'Zones a toilet must not occupy.' },
  { key: 'stairs', label: 'Staircase', hint: 'Preferred zones for the stair.' },
  { key: 'tank', label: 'Water tank', hint: 'Preferred corner for the overhead tank.' },
];

// ---------------------------------------------------------------------------
// Rooms
// ---------------------------------------------------------------------------

/**
 * One requested room (or a grouped request: `{type: 'balcony', count: 2}`).
 * Superset of the LLM parser's ROOM_REQUEST_SCHEMA — the extra per-room
 * preference fields are the F2 "target size OR AI decides, floor, facing,
 * adjacency wishes". Absent/null preference = "AI decides".
 */
export interface RoomRequest {
  readonly type: string;
  readonly count: number;
  readonly notes?: string | undefined;
  /** Integer mm². Absent or null = "AI decides". */
  readonly targetAreaMm2?: number | null | undefined;
  /** 0 = ground floor. Absent or null = "AI decides". */
  readonly floor?: number | null | undefined;
  readonly facing?: Direction8 | null | undefined;
  /** Room-type keys this room wants to sit next to. */
  readonly adjacentTo?: readonly string[] | undefined;
  /** Bedrooms only: attached or common bath. */
  readonly bath?: BathChoice | null | undefined;
}

/** Parser-emitted adjacency wish (kept verbatim; the form renders it read-only). */
export interface AdjacencyWish {
  readonly a: string;
  readonly b: string;
  readonly strength: 'required' | 'preferred' | 'avoid';
}

/** Bedroom types the bedroom editor manages, in canonical order. */
export const BEDROOM_TYPES: readonly string[] = ['bedroom_master', 'bedroom'];

/** The toggleable single rooms of the F2 list (guest/servant are bedrooms with a type). */
export const OPTIONAL_ROOM_TYPES = [
  'pooja',
  'study',
  'guest_bedroom',
  'servant_room',
  'store',
  'utility',
  'garage',
  'balcony',
] as const;
export type OptionalRoomType = (typeof OPTIONAL_ROOM_TYPES)[number];

/** Rooms that may carry more than one instance in the form. */
export const COUNTABLE_ROOM_TYPES: ReadonlySet<string> = new Set(['balcony', 'garage']);

/** Human label for any room-type key, including ones the model does not know. */
export function roomTypeLabel(type: string): string {
  const label = (ROOM_TYPE_LABELS as Record<string, string>)[type];
  return label ?? type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// The typed view over brief.data
// ---------------------------------------------------------------------------

export interface BriefData {
  readonly familySize?: number | undefined;
  /** Total storeys including ground: 2 = G+1 (parser convention). */
  readonly storeys?: number | undefined;
  readonly hasStilt?: boolean | undefined;
  readonly hasBasement?: boolean | undefined;
  readonly terraceAccess?: boolean | undefined;
  readonly futureExpansion?: boolean | undefined;
  readonly parkingCount?: number | undefined;
  readonly kitchenType?: KitchenType | undefined;
  readonly livingDining?: LivingDining | undefined;
  readonly rooms?: readonly RoomRequest[] | undefined;
  readonly adjacencies?: readonly AdjacencyWish[] | undefined;
  readonly styleKitId?: StyleKitId | null | undefined;
  /**
   * Reference-image slot. Phase 2 stores only the chosen file's name as a
   * note; the actual upload (and its use by the facade generator) is Phase 5.
   */
  readonly styleReferenceName?: string | null | undefined;
  readonly budgetBand?: BudgetBandId | undefined;
  /** Whole rupees — never decimals (parser convention). */
  readonly budgetInr?: number | undefined;
  readonly ratePerSqftInr?: number | undefined;
  /** True once the architect explicitly chose a Vastu mode (incl. "off"). */
  readonly vastuDecided?: boolean | undefined;
  readonly vastuPrefs?: VastuPrefs | undefined;
  readonly notes?: string | undefined;
}

// ---------------------------------------------------------------------------
// Tolerant readers — brief.data is a JsonObject and owes us nothing
// ---------------------------------------------------------------------------

function asInt(v: JsonValue | undefined): number | undefined {
  return typeof v === 'number' && Number.isSafeInteger(v) ? v : undefined;
}
function asBool(v: JsonValue | undefined): boolean | undefined {
  return typeof v === 'boolean' ? v : undefined;
}
function asString(v: JsonValue | undefined): string | undefined {
  return typeof v === 'string' ? v : undefined;
}
function asEnum<T extends string>(v: JsonValue | undefined, allowed: readonly T[]): T | undefined {
  return typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : undefined;
}
function asDirection(v: JsonValue | undefined): Direction8 | undefined {
  return asEnum(v, DIRECTIONS_8);
}
function asDirectionList(v: JsonValue | undefined): Direction8[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out: Direction8[] = [];
  for (const item of v) {
    const d = asDirection(item);
    if (d !== undefined) out.push(d);
  }
  return out;
}
function isJsonObject(v: JsonValue | undefined): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function readRoom(v: JsonValue): RoomRequest | undefined {
  if (!isJsonObject(v)) return undefined;
  const type = asString(v['type']);
  const count = asInt(v['count']);
  if (type === undefined || count === undefined || count < 0) return undefined;
  const notes = asString(v['notes']);
  const target = v['targetAreaMm2'] === null ? null : asInt(v['targetAreaMm2']);
  const floor = v['floor'] === null ? null : asInt(v['floor']);
  const facing = v['facing'] === null ? null : asDirection(v['facing']);
  const bath = v['bath'] === null ? null : asEnum(v['bath'], BATH_CHOICES);
  const adjacentTo = Array.isArray(v['adjacentTo'])
    ? v['adjacentTo'].filter((x): x is string => typeof x === 'string')
    : undefined;
  return {
    type,
    count,
    ...(notes === undefined ? {} : { notes }),
    ...(target === undefined ? {} : { targetAreaMm2: target }),
    ...(floor === undefined ? {} : { floor }),
    ...(facing === undefined ? {} : { facing }),
    ...(bath === undefined ? {} : { bath }),
    ...(adjacentTo === undefined ? {} : { adjacentTo }),
  };
}

function readRooms(v: JsonValue | undefined): RoomRequest[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out: RoomRequest[] = [];
  for (const item of v) {
    const room = readRoom(item);
    if (room !== undefined) out.push(room);
  }
  return out;
}

function readAdjacencies(v: JsonValue | undefined): AdjacencyWish[] | undefined {
  if (!Array.isArray(v)) return undefined;
  const out: AdjacencyWish[] = [];
  for (const item of v) {
    if (!isJsonObject(item)) continue;
    const a = asString(item['a']);
    const b = asString(item['b']);
    const strength = asEnum(item['strength'], ['required', 'preferred', 'avoid'] as const);
    if (a !== undefined && b !== undefined && strength !== undefined) out.push({ a, b, strength });
  }
  return out;
}

function readVastuPrefs(v: JsonValue | undefined): VastuPrefs | undefined {
  if (!isJsonObject(v)) return undefined;
  const read = (key: VastuZoneKey): readonly Direction8[] =>
    asDirectionList(v[key]) ?? VASTU_DEFAULT_PREFS[key];
  return {
    entrance: read('entrance'),
    pooja: read('pooja'),
    kitchen: read('kitchen'),
    master: read('master'),
    toilets: read('toilets'),
    toiletsNever: read('toiletsNever'),
    stairs: read('stairs'),
    tank: read('tank'),
    brahmasthanOpen: asBool(v['brahmasthanOpen']) ?? VASTU_DEFAULT_PREFS.brahmasthanOpen,
  };
}

/**
 * Read `brief.data` into the typed view. Never throws; unreadable fields are
 * simply absent, which every consumer already treats as "not answered yet".
 */
export function readBriefData(data: JsonObject): BriefData {
  const styleKitId =
    data['styleKitId'] === null
      ? null
      : asEnum(
          data['styleKitId'],
          STYLE_KITS.map((k) => k.id),
        );
  const styleReferenceName =
    data['styleReferenceName'] === null ? null : asString(data['styleReferenceName']);
  const out: BriefData = {
    ...(asInt(data['familySize']) === undefined ? {} : { familySize: asInt(data['familySize']) }),
    ...(asInt(data['storeys']) === undefined ? {} : { storeys: asInt(data['storeys']) }),
    ...(asBool(data['hasStilt']) === undefined ? {} : { hasStilt: asBool(data['hasStilt']) }),
    ...(asBool(data['hasBasement']) === undefined ? {} : { hasBasement: asBool(data['hasBasement']) }),
    ...(asBool(data['terraceAccess']) === undefined
      ? {}
      : { terraceAccess: asBool(data['terraceAccess']) }),
    ...(asBool(data['futureExpansion']) === undefined
      ? {}
      : { futureExpansion: asBool(data['futureExpansion']) }),
    ...(asInt(data['parkingCount']) === undefined ? {} : { parkingCount: asInt(data['parkingCount']) }),
    ...(asEnum(data['kitchenType'], KITCHEN_TYPES) === undefined
      ? {}
      : { kitchenType: asEnum(data['kitchenType'], KITCHEN_TYPES) }),
    ...(asEnum(data['livingDining'], LIVING_DINING_CHOICES) === undefined
      ? {}
      : { livingDining: asEnum(data['livingDining'], LIVING_DINING_CHOICES) }),
    ...(readRooms(data['rooms']) === undefined ? {} : { rooms: readRooms(data['rooms']) }),
    ...(readAdjacencies(data['adjacencies']) === undefined
      ? {}
      : { adjacencies: readAdjacencies(data['adjacencies']) }),
    ...(styleKitId === undefined ? {} : { styleKitId }),
    ...(styleReferenceName === undefined ? {} : { styleReferenceName }),
    ...(asEnum(
      data['budgetBand'],
      BUDGET_BANDS.map((b) => b.id),
    ) === undefined
      ? {}
      : {
          budgetBand: asEnum(
            data['budgetBand'],
            BUDGET_BANDS.map((b) => b.id),
          ),
        }),
    ...(asInt(data['budgetInr']) === undefined ? {} : { budgetInr: asInt(data['budgetInr']) }),
    ...(asInt(data['ratePerSqftInr']) === undefined
      ? {}
      : { ratePerSqftInr: asInt(data['ratePerSqftInr']) }),
    ...(asBool(data['vastuDecided']) === undefined
      ? {}
      : { vastuDecided: asBool(data['vastuDecided']) }),
    ...(readVastuPrefs(data['vastuPrefs']) === undefined
      ? {}
      : { vastuPrefs: readVastuPrefs(data['vastuPrefs']) }),
    ...(asString(data['notes']) === undefined ? {} : { notes: asString(data['notes']) }),
  };
  return out;
}

// ---------------------------------------------------------------------------
// Room-list helpers — every write path funnels through normaliseRooms
// ---------------------------------------------------------------------------

const ROOM_TYPE_ORDER: ReadonlyMap<string, number> = new Map(
  ROOM_TYPES.map((t: RoomType, i: number) => [t, i]),
);

function roomOrder(type: string): number {
  // Bedrooms first (master before others) so the form and the parser agree on
  // which entry "Bedroom 1" is; unknown types sink to the end, alphabetically.
  if (type === 'bedroom_master') return -2;
  if (type === 'bedroom') return -1;
  return ROOM_TYPE_ORDER.get(type) ?? ROOM_TYPES.length;
}

/**
 * Canonicalise a rooms array:
 *  - bedrooms (`bedroom_master` / `bedroom`) become ONE ENTRY PER ROOM so each
 *    can carry its own bath/floor/facing/size preferences;
 *  - other types collapse to one grouped entry (counts summed, first entry's
 *    preferences win);
 *  - zero-count entries drop;
 *  - stable canonical order, so two edits that mean the same thing serialise
 *    the same way (merge patches replace this array wholesale — RFC 7386).
 */
export function normaliseRooms(rooms: readonly RoomRequest[]): RoomRequest[] {
  const bedrooms: RoomRequest[] = [];
  const grouped = new Map<string, RoomRequest>();

  for (const room of rooms) {
    if (room.count <= 0) continue;
    if (BEDROOM_TYPES.includes(room.type)) {
      for (let i = 0; i < room.count; i += 1) bedrooms.push({ ...room, count: 1 });
      continue;
    }
    const existing = grouped.get(room.type);
    grouped.set(
      room.type,
      existing === undefined ? room : { ...existing, count: existing.count + room.count },
    );
  }

  // Exactly one master: the first bedroom is the master by convention.
  const sortedBedrooms = bedrooms
    .sort((a, b) => roomOrder(a.type) - roomOrder(b.type))
    .map((room, i) => ({ ...room, type: i === 0 ? 'bedroom_master' : 'bedroom' }));

  const others = [...grouped.values()].sort(
    (a, b) => roomOrder(a.type) - roomOrder(b.type) || a.type.localeCompare(b.type),
  );
  return [...sortedBedrooms, ...others];
}

/** Total count of a room type across entries. */
export function roomCount(rooms: readonly RoomRequest[] | undefined, type: string): number {
  let total = 0;
  for (const room of rooms ?? []) if (room.type === type) total += room.count;
  return total;
}

/** Bedrooms (master first), one row per room — the bedroom editor's model. */
export function bedroomRows(rooms: readonly RoomRequest[] | undefined): RoomRequest[] {
  return normaliseRooms(rooms ?? []).filter((r) => BEDROOM_TYPES.includes(r.type));
}

/** Non-bedroom entries after normalisation. */
export function otherRooms(rooms: readonly RoomRequest[] | undefined): RoomRequest[] {
  return normaliseRooms(rooms ?? []).filter((r) => !BEDROOM_TYPES.includes(r.type));
}

/** Replace the grouped entry for `type` (count 0 removes it). */
export function setRoomCount(
  rooms: readonly RoomRequest[] | undefined,
  type: string,
  count: number,
  prefs: Partial<RoomRequest> = {},
): RoomRequest[] {
  const rest = (rooms ?? []).filter((r) => r.type !== type);
  const existing = (rooms ?? []).find((r) => r.type === type);
  if (count <= 0) return normaliseRooms(rest);
  return normaliseRooms([...rest, { ...existing, ...prefs, type, count }]);
}

/** Patch one bedroom row by its position in `bedroomRows`. */
export function updateBedroom(
  rooms: readonly RoomRequest[] | undefined,
  index: number,
  patch: Partial<RoomRequest>,
): RoomRequest[] {
  const beds = bedroomRows(rooms);
  const row = beds[index];
  if (row === undefined) return normaliseRooms(rooms ?? []);
  beds[index] = { ...row, ...patch, count: 1 };
  return normaliseRooms([...beds, ...otherRooms(rooms)]);
}

/** Append a bedroom (the first ever added becomes the master). */
export function addBedroom(rooms: readonly RoomRequest[] | undefined): RoomRequest[] {
  return normaliseRooms([...(rooms ?? []), { type: 'bedroom', count: 1, bath: 'common' }]);
}

/** Remove the bedroom row at `index` (master re-derives — first row wins). */
export function removeBedroom(
  rooms: readonly RoomRequest[] | undefined,
  index: number,
): RoomRequest[] {
  const beds = bedroomRows(rooms);
  if (index < 0 || index >= beds.length) return normaliseRooms(rooms ?? []);
  beds.splice(index, 1);
  return normaliseRooms([...beds, ...otherRooms(rooms)]);
}

/**
 * Apply the living/dining choice to the rooms list: `combined` keeps one
 * `living_dining`, `separate` keeps `living` + `dining`.
 */
export function withLivingDining(
  rooms: readonly RoomRequest[] | undefined,
  choice: LivingDining,
): RoomRequest[] {
  const rest = (rooms ?? []).filter((r) => !['living', 'dining', 'living_dining'].includes(r.type));
  const added: RoomRequest[] =
    choice === 'combined'
      ? [{ type: 'living_dining', count: 1 }]
      : [
          { type: 'living', count: 1 },
          { type: 'dining', count: 1 },
        ];
  return normaliseRooms([...rest, ...added]);
}

// ---------------------------------------------------------------------------
// Budget helpers
// ---------------------------------------------------------------------------

/**
 * Derived area target in integer mm² — `budget ÷ rate` sq ft, rounded once at
 * the boundary. Returns null when either input is missing/zero.
 */
export function areaTargetMm2(
  budgetInr: number | undefined,
  ratePerSqftInr: number | undefined,
): number | null {
  if (budgetInr === undefined || budgetInr <= 0) return null;
  const rate = ratePerSqftInr ?? DEFAULT_RATE_PER_SQFT_INR;
  if (rate <= 0) return null;
  return fromSqft(budgetInr / rate);
}

/**
 * Parse the ways an Indian client states money: "45,00,000", "₹45L",
 * "1.2 Cr", "85 lakh", "60 lac", "8500000". Whole rupees, integer.
 * Returns null (never throws) — form fields want a quiet failure.
 */
export function parseRupees(raw: string): number | null {
  const s = raw
    .replace(/[₹,\s]/g, '')
    .trim()
    .toLowerCase();
  if (s === '') return null;
  const m = /^([0-9]*\.?[0-9]+)(cr|crore|crores|l|lac|lacs|lakh|lakhs|k)?$/.exec(s);
  if (!m || m[1] === undefined) return null;
  const n = Number(m[1]);
  if (!Number.isFinite(n) || n < 0) return null;
  const unit = m[2] ?? '';
  const factor = unit.startsWith('cr') ? 10_000_000 : unit === 'k' ? 1_000 : unit === '' ? 1 : 100_000;
  return roundHalfAwayFromZero(n * factor);
}

/** Band whose midpoint window contains `budgetInr`, for keeping band ↔ ₹ in step. */
export function bandForBudget(budgetInr: number): BudgetBandId {
  if (budgetInr < 2_500_000) return 'under-25l';
  if (budgetInr < 5_000_000) return '25l-50l';
  if (budgetInr < 10_000_000) return '50l-1cr';
  if (budgetInr < 20_000_000) return '1cr-2cr';
  return 'over-2cr';
}
