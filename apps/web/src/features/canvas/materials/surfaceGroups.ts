/**
 * surfaceGroups.ts — THE surface-group → mesh mapping (Phase 5 §8).
 *
 * Two contracts live here, both pure and both pinned by `mapping.test.ts`:
 *
 *  1. `surfaceGroupOf(element)` — which of the model's `SURFACE_GROUPS` a
 *     rendered mesh belongs to. The 3D scene builder calls this per mesh and
 *     then asks `resolve.ts` what colour that group wears. One function, so
 *     the assignment UI and the renderer can never disagree about what
 *     "external walls" means.
 *
 *  2. `SURFACE_PICKS` — the five groups the panel offers (task contract:
 *     external walls / internal walls / floor / railing / trim), each with a
 *     filter into the material catalogue's OWN vocabulary. The catalogue's
 *     `surfaceGroups` strings are dotted application areas
 *     (`wall.exterior`, `floor.bath`, `facade.cladding` — see
 *     `fixtures/catalog/materials.json`), which are NOT the model's enum;
 *     this file is the one place the two vocabularies meet.
 *
 * `material.assign` targets only ever carry the model enum — the catalogue
 * vocabulary never enters an op payload.
 */

import type {
  FacadeComponentKind,
  HouseModel,
  OpeningKind,
  SlabKind,
  SurfaceGroup,
  WallKind,
} from '@garh/model';
import type { MaterialItem } from '../../../lib/schemas';

// ---------------------------------------------------------------------------
// 1. Element → model surface group
// ---------------------------------------------------------------------------

/** What the 3D scene knows about a mesh when it asks for its group. */
export type SurfaceElement =
  | { readonly kind: 'wall'; readonly wallKind: WallKind }
  | { readonly kind: 'slab'; readonly slabKind: SlabKind }
  | { readonly kind: 'stair' }
  | { readonly kind: 'balconySlab' }
  | { readonly kind: 'balconyRailing' }
  | { readonly kind: 'opening'; readonly openingKind: OpeningKind }
  | { readonly kind: 'column' }
  | { readonly kind: 'facade'; readonly componentKind: FacadeComponentKind };

const WALL_GROUPS: Readonly<Record<WallKind, SurfaceGroup>> = {
  external: 'external_wall',
  internal: 'internal_wall',
  parapet: 'parapet',
};

const SLAB_GROUPS: Readonly<Record<SlabKind, SurfaceGroup>> = {
  floor: 'floor',
  terrace: 'roof',
  plinth: 'plinth',
  mumty: 'external_wall', // a small room over the stair, finished like one
};

const FACADE_GROUPS: Readonly<Record<FacadeComponentKind, SurfaceGroup>> = {
  window_trim: 'cladding',
  chajja: 'external_wall', // an RCC projection wears the exterior finish
  parapet_profile: 'parapet',
  cladding_zone: 'cladding',
  porch: 'external_wall',
  railing: 'railing',
  band: 'cladding',
  louver: 'cladding',
  entry_feature: 'cladding',
};

/** The mapping. Total over `SurfaceElement`; the spec walks every arm. */
export function surfaceGroupOf(element: SurfaceElement): SurfaceGroup {
  switch (element.kind) {
    case 'wall':
      return WALL_GROUPS[element.wallKind];
    case 'slab':
      return SLAB_GROUPS[element.slabKind];
    case 'stair':
      return 'staircase';
    case 'balconySlab':
      return 'floor';
    case 'balconyRailing':
      return 'railing';
    case 'opening':
      return element.openingKind === 'door' ? 'door' : 'window';
    case 'column':
      // Columns read as wall surfaces; interior paint is the safer default.
      return 'internal_wall';
    case 'facade':
      return FACADE_GROUPS[element.componentKind];
  }
}

// ---------------------------------------------------------------------------
// 2. The panel's five picks, filtered into the catalogue vocabulary
// ---------------------------------------------------------------------------

export interface SurfacePick {
  readonly group: SurfaceGroup;
  readonly label: string;
  /** One honest line about what this recolours. */
  readonly hint: string;
  /** Catalogue `surfaceGroups` prefixes that belong under this pick. */
  readonly catalogPrefixes: readonly string[];
}

/** The five surface groups the panel offers (task contract). */
export const SURFACE_PICKS: readonly SurfacePick[] = [
  {
    group: 'external_wall',
    label: 'External walls',
    hint: 'Outside faces, chajjas and porches.',
    catalogPrefixes: ['wall.exterior', 'wall.feature'],
  },
  {
    group: 'internal_wall',
    label: 'Internal walls',
    hint: 'Inside faces and columns.',
    catalogPrefixes: ['wall.interior', 'wall.bath', 'wall.kitchen'],
  },
  {
    group: 'floor',
    label: 'Floors',
    hint: 'Floor slabs and balcony decks.',
    catalogPrefixes: ['floor.'],
  },
  {
    group: 'railing',
    label: 'Railings',
    hint: 'Balcony and stair railings.',
    catalogPrefixes: ['railing.'],
  },
  {
    group: 'cladding',
    label: 'Trim & cladding',
    hint: 'Facade bands, trims, louvers and cladding zones.',
    catalogPrefixes: ['facade.'],
  },
];

export function surfacePickFor(group: SurfaceGroup): SurfacePick | null {
  return SURFACE_PICKS.find((p) => p.group === group) ?? null;
}

/**
 * The surface group an ELEMENT ID belongs to, by looking it up in the model —
 * the bridge from "what the architect clicked in the canvas" to "which
 * shelf of the catalogue applies to it", for the panel's per-element scope.
 * Null when the id is not an element this panel can paint (a room, a deleted
 * id, a facade component — the facade has its own inspector).
 *
 * Deliberately built on {@link surfaceGroupOf}, so the element scope and the
 * renderer can never disagree about what a wall is.
 */
export function surfaceGroupOfElement(house: HouseModel, id: string): SurfaceGroup | null {
  const wall = house.walls.find((w) => w.id === id);
  if (wall !== undefined) return surfaceGroupOf({ kind: 'wall', wallKind: wall.kind });
  const opening = house.openings.find((o) => o.id === id);
  if (opening !== undefined) {
    return surfaceGroupOf({ kind: 'opening', openingKind: opening.kind });
  }
  const slab = house.slabs.find((s) => s.id === id);
  if (slab !== undefined) return surfaceGroupOf({ kind: 'slab', slabKind: slab.kind });
  if (house.stairs.some((s) => s.id === id)) return surfaceGroupOf({ kind: 'stair' });
  // A balcony is two surfaces; its deck is the one an architect means by
  // "this balcony's material" (the railing follows the facade kit).
  if (house.balconies.some((b) => b.id === id)) return surfaceGroupOf({ kind: 'balconySlab' });
  if (house.columns.some((c) => c.id === id)) return surfaceGroupOf({ kind: 'column' });
  return null;
}

/**
 * Does a catalogue item belong under a pick?
 *
 * Prefix match against the item's declared application areas. An item that
 * declares NO areas matches every pick — a degraded catalogue payload should
 * show everything rather than hide everything (the schema defaults
 * `surfaceGroups` to `[]`, so this is reachable, not theoretical).
 */
export function materialMatchesPick(item: MaterialItem, pick: SurfacePick): boolean {
  if (item.surfaceGroups.length === 0) return true;
  return item.surfaceGroups.some((area) =>
    pick.catalogPrefixes.some((prefix) =>
      prefix.endsWith('.')
        ? area.startsWith(prefix)
        : area === prefix || area.startsWith(`${prefix}.`),
    ),
  );
}

/** The pick's slice of the catalogue, in catalogue order. */
export function materialsForPick(
  items: readonly MaterialItem[],
  pick: SurfacePick,
): MaterialItem[] {
  return items.filter((item) => materialMatchesPick(item, pick));
}
