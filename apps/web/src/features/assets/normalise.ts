/**
 * Both catalogues → {@link AssetRecord}.
 *
 * Furniture arrives already normalised: `features/canvas/furniture/catalogue.ts`
 * owns that transform (it resolves the clearance fallback and narrows the
 * category), and this feature consumes its output rather than re-deriving it.
 * Two normalisers for one endpoint is exactly how a browser and a canvas end up
 * disagreeing about what "600 mm deep" means.
 *
 * Materials arrive raw, because the material feature does not have a normaliser
 * — `useMaterialsCatalogue` hands back the zod-parsed `MaterialItem` as served.
 *
 * ## What a material does not have
 *
 * `materialItemSchema` (`lib/schemas.ts`) carries id, name, category, colorHex,
 * textureUrl and surfaceGroups. The seed file also has `finish`, `texture` and
 * `priceInrPerSqm`; zod strips unknown keys, so those never reach the browser
 * and this module does not pretend otherwise. Adding them is one line in
 * `furnitureItemSchema`'s neighbour and they would flow straight through the
 * search haystack below — that file belongs to the integrator, so it is an ask,
 * not an edit.
 */

import type { MaterialItem } from '../../lib/schemas';
import type { CatalogueItem } from '../canvas/furniture/types';
import { categoryLabelFor, toMaterialCategory, type AssetRecord } from './types';

export function furnitureRecord(item: CatalogueItem): AssetRecord {
  return {
    key: `furniture:${item.id}`,
    kind: 'furniture',
    id: item.id,
    name: item.name,
    category: item.category,
    categoryKey: `furniture:${item.category}`,
    categoryLabel: categoryLabelFor('furniture', item.category),
    roomTypes: item.roomTypes,
    surfaceGroups: [],
    widthMm: item.widthMm,
    depthMm: item.depthMm,
    heightMm: item.heightMm,
    clearanceMm: item.clearanceMm,
    clearanceAssumed: item.clearanceAssumed,
    swatchHex: null,
  };
}

export function materialRecord(item: MaterialItem): AssetRecord {
  const category = toMaterialCategory(item.category);
  return {
    key: `material:${item.id}`,
    kind: 'material',
    id: item.id,
    name: item.name,
    category,
    categoryKey: `material:${category}`,
    categoryLabel: categoryLabelFor('material', category),
    roomTypes: [],
    surfaceGroups: item.surfaceGroups,
    // Null, not zero — a material has no footprint, and zero would silently
    // satisfy every "fits in N mm" test. See the header of `types.ts`.
    widthMm: null,
    depthMm: null,
    heightMm: null,
    clearanceMm: null,
    clearanceAssumed: false,
    swatchHex: item.colorHex,
  };
}

export function toAssetRecords(
  furniture: readonly CatalogueItem[],
  materials: readonly MaterialItem[],
): AssetRecord[] {
  const out: AssetRecord[] = [];
  for (const item of furniture) out.push(furnitureRecord(item));
  for (const item of materials) out.push(materialRecord(item));
  return out;
}
