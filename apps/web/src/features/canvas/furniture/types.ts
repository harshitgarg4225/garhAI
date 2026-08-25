/**
 * Furniture feature — the vocabulary every other file in this folder shares.
 *
 * ## The one coordinate contract
 *
 * A catalogue item has a LOCAL frame with its footprint centre at the origin:
 *
 *                       +Y  (the item's FRONT — where the clearance strip goes)
 *                        ^
 *            +-----------+-----------+   ---
 *            |                       |    |
 *      -X <--+           o           +--> +X    depthMm
 *            |                       |    |
 *            +-----------------------+   ---
 *            |<------ widthMm ------>|
 *
 *   widthMm   spans local X
 *   depthMm   spans local Y
 *   heightMm  spans local Z, measured up from the floor of the storey
 *
 * `rotationDeg` is integer degrees **counter-clockwise** from that frame — the
 * same convention `furniture.set` uses (`packages/model/src/ops.ts`, op 25) and
 * the same one `FurnitureInstance.rotationDeg` stores. `pt` is the footprint
 * centre in plot-local integer millimetres.
 *
 * Choosing +Y as "the front" matters, because it is where the access strip is
 * drawn and where the solver's fit test spends its clearance:
 * `services/solver/furniture_fit.py` packs each item as
 * `(width, depth + clearance)`, so the strip lives along the depth axis. This
 * module puts it on the +Y side of that axis and nowhere else. If the two ever
 * disagree, a bed that "fits" in the solver stops fitting in the editor, which
 * is exactly the class of bug the integer-mm rule exists to prevent.
 *
 * ## Integer millimetres
 *
 * Everything here is `int` mm or `int` degrees. Screen-space floats live in the
 * renderer (`FurnitureLayer.tsx`) and die at `ops.ts`, which is the only file
 * that builds op payloads. See the header of each for the exact boundary.
 */

import type { Pt, RoomType } from '@garh/model';

// ---------------------------------------------------------------------------
// Catalogue
// ---------------------------------------------------------------------------

/**
 * The nine catalogue groups the seeded 45 items actually use, in the order the
 * browser lists them: what you sit or sleep on first, services last.
 *
 * `other` is not in the seed data. It is the landing pad for a category the
 * server adds later — an unknown group renders in its own section rather than
 * disappearing from a browser that hard-codes nine strings.
 */
export const FURNITURE_CATEGORIES = [
  'bed',
  'seating',
  'table',
  'storage',
  'kitchen',
  'sanitary',
  'appliance',
  'vehicle',
  'service',
  'other',
] as const;
export type FurnitureCategory = (typeof FURNITURE_CATEGORIES)[number];

export const FURNITURE_CATEGORY_LABELS: Readonly<Record<FurnitureCategory, string>> = {
  bed: 'Beds',
  seating: 'Seating',
  table: 'Tables & desks',
  storage: 'Storage',
  kitchen: 'Kitchen',
  sanitary: 'Bath & sanitary',
  appliance: 'Appliances',
  vehicle: 'Vehicles',
  service: 'Services',
  other: 'Other',
};

/**
 * One catalogue entry, normalised for the editor.
 *
 * This is `FurnitureItem` from `lib/schemas.ts` (what `GET /catalog/furniture`
 * parses to) plus the two things the placement tool cannot work without:
 * a resolved `clearanceMm`, and a `category` narrowed to the closed list above.
 */
export interface CatalogueItem {
  readonly id: string;
  readonly name: string;
  readonly category: FurnitureCategory;
  /** Raw `category` string as served, kept for search and for round-tripping. */
  readonly rawCategory: string;
  /** Local +X extent, integer mm. */
  readonly widthMm: number;
  /** Local +Y extent, integer mm. */
  readonly depthMm: number;
  /** Local +Z extent (above floor), integer mm. */
  readonly heightMm: number;
  /**
   * Free space this item needs in FRONT of it (+Y) to be usable: a wardrobe
   * needs 750 mm to open, a bed 600 mm to walk past. Integer mm; 0 means the
   * item genuinely needs none (a wall shelf, a shower tray).
   */
  readonly clearanceMm: number;
  /**
   * True when `clearanceMm` came from {@link CLEARANCE_FALLBACK_MM} rather than
   * from the server. Surfaced as an assumption chip — §15 golden rule 4 says a
   * default the app invented is visible, not silent.
   */
  readonly clearanceAssumed: boolean;
  /** Room types this item belongs in. Drives the browser's room filter. */
  readonly roomTypes: readonly string[];
  /**
   * A real 3D asset, when one exists. Always `null` today — see the honesty
   * note in `proxyMesh.ts`. Phase 5/7 fills this in.
   */
  readonly assetUrl: string | null;
}

// ---------------------------------------------------------------------------
// Placement
// ---------------------------------------------------------------------------

/** Where an item sits: footprint centre + integer-degree rotation. */
export interface Pose {
  readonly pt: Pt;
  /** Integer degrees CCW, normalised to [0, 360). */
  readonly rotationDeg: number;
}

/**
 * A placed instance joined to its catalogue entry — what the renderer and the
 * collision pass both iterate. `item` is null when the model references a
 * catalogue id the server no longer serves; the instance still renders (as a
 * question-mark box) rather than vanishing from a drawing someone submitted.
 */
export interface PlacedFurniture {
  readonly id: string;
  readonly storeyId: string;
  readonly catalogId: string;
  readonly pose: Pose;
  readonly item: CatalogueItem | null;
}

/** Why a preview is showing amber. Advisory only — placement never blocks. */
export type PlacementIssueCode =
  | 'overlaps-furniture'
  | 'overlaps-wall'
  | 'clearance-blocked'
  | 'outside-room'
  | 'unexpected-room';

export type PlacementIssueSeverity = 'info' | 'warn';

/**
 * One advisory about the current preview pose.
 *
 * `basis` deliberately replaces the "cite" a compliance chip carries. These are
 * furnishing-practice observations computed from catalogue clearances — they
 * are NOT NBC or bye-law clauses, and printing a fabricated clause number on a
 * municipal drawing would be worse than printing nothing. When a real rule does
 * apply (a WC door swing, a parking bay size), that check belongs in the rules
 * engine and arrives as a compliance chip with a genuine citation.
 */
export interface PlacementIssue {
  readonly code: PlacementIssueCode;
  readonly severity: PlacementIssueSeverity;
  /** One plain sentence: "Overlaps the Queen bed." */
  readonly message: string;
  /** Where the number came from: "Catalogue clearance: 750 mm access strip." */
  readonly basis: string;
  /** What to do about it: "Rotate with R, or drop it anyway and move it later." */
  readonly fixHint: string;
  /** Element ids involved, so the canvas can highlight them. */
  readonly targetIds: readonly string[];
}

// ---------------------------------------------------------------------------
// Geometry value types
// ---------------------------------------------------------------------------

/**
 * A convex quad in DOUBLED millimetres (1 unit = 0.5 mm), always 4 points.
 *
 * Why doubled: an item 1525 mm wide has half-extents of 762.5 mm, which is not
 * an integer. Working at twice the resolution keeps every corner an exact
 * integer, so the separating-axis test below is exact integer arithmetic with
 * no epsilon and no drift. Corners convert back to mm only for display, in
 * {@link cornersToMm}.
 */
export type Quad2x = readonly [Pt, Pt, Pt, Pt];

/** Axis-aligned bounds in doubled mm — the broad phase of collision. */
export interface Bounds2x {
  readonly minX: number;
  readonly minY: number;
  readonly maxX: number;
  readonly maxY: number;
}

/** Anything the preview can collide with, pre-transformed once per doc change. */
export interface Obstacle {
  readonly id: string;
  readonly kind: 'furniture' | 'wall';
  /** Human name for the advisory sentence: "Queen bed", "wall". */
  readonly label: string;
  readonly quad: Quad2x;
  readonly bounds: Bounds2x;
}

/** The subset of `Room` this feature reads. Keeps tests free of full documents. */
export interface RoomLike {
  readonly id: string;
  readonly type: RoomType;
  readonly name: string;
  readonly polygon: readonly Pt[];
}
