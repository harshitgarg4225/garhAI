/**
 * The asset browser's vocabulary: one record shape covering both catalogues.
 *
 * ## Why one record and not two lists
 *
 * `GET /catalog/furniture` serves 469 items and `GET /catalog/materials` 184.
 * An architect looking for "kota" does not know or care which endpoint it came
 * from, and two side-by-side search boxes would make them care. So both
 * normalise into {@link AssetRecord} and the *kind* becomes a filter, not a
 * navigation decision.
 *
 * ## The consequence, stated rather than hidden
 *
 * A material has no footprint. `widthMm` / `depthMm` / `heightMm` are therefore
 * `null` on a material record, not `0`. Zero would be a lie that quietly
 * satisfies every "fits in 900 mm" test — the same shape as the circulation
 * denominator that drove a cap to zero and switched it off. `null` cannot be
 * compared by accident: `filters.ts` has to decide what a dimensional filter
 * means for a record that has no dimensions, and it says so on screen.
 *
 * ## Integer millimetres
 *
 * Every length here is integer mm, as it arrives from the API. The only float
 * in this feature is a screen coordinate, and there are none of those. User
 * input for the dimensional filters is parsed to mm at the input boundary via
 * `lib/units` (`tryParseLengthMm`), never with `parseFloat`.
 */

import { ROOM_TYPES, ROOM_TYPE_LABELS, type RoomType } from '@garh/model';

import {
  FURNITURE_CATEGORIES,
  FURNITURE_CATEGORY_LABELS,
  type FurnitureCategory,
} from '../canvas/furniture/types';

export type AssetKind = 'furniture' | 'material';

/**
 * The material groups `fixtures/catalog/materials.json` actually uses, in the
 * order a specification is written: what you walk on, then what encloses you,
 * then the openings and the metalwork.
 *
 * `other` mirrors the furniture list's landing pad — a category the server adds
 * later renders in its own group instead of vanishing from a browser that
 * hard-codes six strings.
 */
export const MATERIAL_CATEGORIES = [
  'floor',
  'wall',
  'roof',
  'glazing',
  'joinery',
  'railing',
  'other',
] as const;
export type MaterialCategory = (typeof MATERIAL_CATEGORIES)[number];

export const MATERIAL_CATEGORY_LABELS: Readonly<Record<MaterialCategory, string>> = {
  floor: 'Flooring',
  wall: 'Wall finishes',
  roof: 'Roofing',
  glazing: 'Glazing',
  joinery: 'Joinery',
  railing: 'Railings',
  other: 'Other materials',
};

/**
 * One browsable thing, from either catalogue.
 *
 * `key` is `${kind}:${id}`. Ids are only unique *within* a catalogue, and
 * favourites, recents and React keys all index across both — so the namespaced
 * key is the identity everywhere outside the two normalisers.
 */
export interface AssetRecord {
  readonly key: string;
  readonly kind: AssetKind;
  /** Catalogue id, as served. This is what a placement op or a material set stores. */
  readonly id: string;
  readonly name: string;
  /** Narrowed category slug within the kind — `bed`, `floor`, … */
  readonly category: string;
  /** `${kind}:${category}` — unique across both catalogues, for the facet list. */
  readonly categoryKey: string;
  readonly categoryLabel: string;
  /** Room types this belongs in. Empty for most materials. */
  readonly roomTypes: readonly string[];
  /** Surfaces a material may be applied to. Empty for furniture. */
  readonly surfaceGroups: readonly string[];
  /** Local +X extent, integer mm. `null` when the kind has no footprint. */
  readonly widthMm: number | null;
  /** Local +Y extent, integer mm. `null` when the kind has no footprint. */
  readonly depthMm: number | null;
  /** Local +Z extent, integer mm. `null` when the kind has no footprint. */
  readonly heightMm: number | null;
  /**
   * Access strip needed in FRONT of the item (+Y), integer mm; `null` for a
   * material. Same number the solver packs as `depth + clearance`
   * (`services/solver/furniture_fit.py`) — see the note in `filters.ts`.
   */
  readonly clearanceMm: number | null;
  /** True when `clearanceMm` was assumed rather than served. Shown, not hidden. */
  readonly clearanceAssumed: boolean;
  /** Swatch colour for a material, when the API served one. */
  readonly swatchHex: string | null;
}

/** Narrowing guard: a record the dimensional filters can actually answer for. */
export function hasFootprint(
  record: AssetRecord,
): record is AssetRecord & { widthMm: number; depthMm: number; clearanceMm: number } {
  return record.widthMm !== null && record.depthMm !== null && record.clearanceMm !== null;
}

/**
 * Which slice of the library the list is showing.
 *
 * A scope rather than three more boolean filters: "favourites" and "recent" are
 * *different lists*, not narrower versions of the same one — recent has its own
 * order (last used first) and its own empty state, and a user who has favourited
 * nothing needs to be told that, not shown "0 results".
 */
export type AssetScope = 'all' | 'favourites' | 'recent';

export interface AssetFilters {
  readonly kind: AssetKind | 'all';
  readonly scope: AssetScope;
  /** `${kind}:${category}`, or null for every category. */
  readonly categoryKey: string | null;
  /** A room type slug, or null. Matched against `AssetRecord.roomTypes`. */
  readonly roomType: string | null;
  /** "fits in N mm, front to back". Integer mm, or null for off. */
  readonly maxDepthMm: number | null;
  /** "fits in N mm along the wall". Integer mm, or null for off. */
  readonly maxWidthMm: number | null;
  /**
   * Whether the depth test spends the item's access strip as well as its body.
   * Default true — see the long note in `filters.ts` for why that is the honest
   * default and why it is a visible switch rather than a constant.
   */
  readonly includeClearance: boolean;
}

export const DEFAULT_FILTERS: AssetFilters = {
  kind: 'all',
  scope: 'all',
  categoryKey: null,
  roomType: null,
  maxDepthMm: null,
  maxWidthMm: null,
  includeClearance: true,
};

/**
 * True when at least one NARROWING filter is on.
 *
 * `kind` and `scope` are deliberately not counted. They are navigation — which
 * half of the library you are looking at — and "Clear filters" does not reset
 * them, so counting them here would leave the button on screen after a clear,
 * offering an action that changes nothing. The set this answers for is exactly
 * the set that button resets.
 */
export function hasNarrowingFilters(filters: AssetFilters): boolean {
  return (
    filters.categoryKey !== null ||
    filters.roomType !== null ||
    filters.maxDepthMm !== null ||
    filters.maxWidthMm !== null
  );
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

const ROOM_TYPE_SET = new Set<string>(ROOM_TYPES);

/** `bedroom_master` → `Master Bedroom`; an unknown slug renders as itself. */
export function roomTypeLabel(slug: string): string {
  return ROOM_TYPE_SET.has(slug) ? ROOM_TYPE_LABELS[slug as RoomType] : slug;
}

const FURNITURE_CATEGORY_SET = new Set<string>(FURNITURE_CATEGORIES);
const MATERIAL_CATEGORY_SET = new Set<string>(MATERIAL_CATEGORIES);

/** Narrow a served furniture category to the closed list; anything else is `other`. */
export function toFurnitureCategory(raw: string): FurnitureCategory {
  const key = raw.trim().toLowerCase();
  return FURNITURE_CATEGORY_SET.has(key) ? (key as FurnitureCategory) : 'other';
}

/** Narrow a served material category to the closed list; anything else is `other`. */
export function toMaterialCategory(raw: string): MaterialCategory {
  const key = raw.trim().toLowerCase();
  return MATERIAL_CATEGORY_SET.has(key) ? (key as MaterialCategory) : 'other';
}

export function categoryLabelFor(kind: AssetKind, category: string): string {
  return kind === 'furniture'
    ? FURNITURE_CATEGORY_LABELS[toFurnitureCategory(category)]
    : MATERIAL_CATEGORY_LABELS[toMaterialCategory(category)];
}

/**
 * Display order across both catalogues: furniture first (it is what a plan tab
 * reaches for), materials after, each in its own declared order. Returned as a
 * rank so the sort in `search.ts` stays a pure numeric comparison.
 */
export function categoryRank(kind: AssetKind, category: string): number {
  if (kind === 'furniture') {
    const idx = FURNITURE_CATEGORIES.indexOf(toFurnitureCategory(category));
    return idx < 0 ? FURNITURE_CATEGORIES.length : idx;
  }
  const idx = MATERIAL_CATEGORIES.indexOf(toMaterialCategory(category));
  return 1000 + (idx < 0 ? MATERIAL_CATEGORIES.length : idx);
}
