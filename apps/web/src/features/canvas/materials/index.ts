/**
 * `features/canvas/materials` — material assignment (op 29) for the 3D view.
 *
 * WHAT THE INTEGRATOR MOUNTS
 *   <MaterialsPanel />    the surface-group → material picker. Dispatches
 *                         `material.assign` through the model store.
 *
 * WHAT THE 3D SCENE BUILDER CALLS (the surface-group → mesh contract)
 *   surfaceGroupOf(el)      which SurfaceGroup a mesh belongs to
 *   resolvedColorHex(...)   the colour that group wears at a context,
 *                           or null for "scene default"
 *   swatchHex(item)         procedural colour for a catalogue material —
 *                           colours only, never textures (asset-gate safe)
 *
 * Materials NEVER touch geometry: op 29 writes `house.materials` rows and
 * nothing else, so a material change must not dirty a storey's mesh cache —
 * only its colors. `mapping.test.ts` and `assignOps.test.ts` pin all of this.
 */

export {
  SURFACE_PICKS,
  materialMatchesPick,
  materialsForPick,
  surfaceGroupOf,
  surfacePickFor,
} from './surfaceGroups';
export type { SurfaceElement, SurfacePick } from './surfaceGroups';

export { fallbackColorFromId, resolveAssignment, resolvedColorHex, swatchHex } from './resolve';
export type { SurfaceContext } from './resolve';

export { assignmentIdFor, materialAssignOp, materialClearOp, surfaceTargetKey } from './assignOps';

export {
  loadMaterialsCatalogue,
  resetMaterialsCatalogueCache,
  useMaterialsCatalogue,
} from './useMaterialsCatalogue';
export type { MaterialsCatalogue, MaterialsLoadable } from './useMaterialsCatalogue';

export { MaterialsPanel } from './MaterialsPanel';
export type { MaterialsPanelProps } from './MaterialsPanel';
