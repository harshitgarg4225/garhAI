/**
 * builtins.ts — the three views nobody should have to save: fit all, fit
 * selection, fit storey.
 *
 * Pure over the model. No three, no React, no controller — the camera these
 * become is `cameraForExtent`'s job, so this file only has to answer "which box
 * are we framing, and how tall is it".
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE DEFINITION OF EACH EXTENT, IMPORTED — NOT A SECOND ONE, WRITTEN
 * ════════════════════════════════════════════════════════════════════════════
 * `planExtentMm` and `elementsExtentMm` already exist (in the plan page's
 * geometry module, whose own header says it belongs in `features/` and knows
 * nothing about being inside a page), and `buildingExtentOf` already exists for
 * the 3D fit and the sun's shadow camera. They are what the F key and the
 * compliance chips fit to.
 *
 * Writing a second "bounding box of the selection" here would be the FAR-in-two
 * places mistake in a different costume: the two would agree in review and
 * drift in a release, and the symptom would be "fit selection frames something
 * slightly different from the chip that jumps to the same wall". So this module
 * imports them and adds nothing but the meaning of the three names.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT EACH NAME MEANS, AND WHY THEY ARE NOT THE SAME BOX
 * ════════════════════════════════════════════════════════════════════════════
 *   Fit all       the whole project: the plot boundary UNION every storey's
 *                 geometry. "All" has to mean all, or the name is a lie — and
 *                 on a plot with a small building it is a visibly different
 *                 view from fitting the building alone.
 *   Fit storey    the storey on screen: its walls, balconies and stairs, or
 *                 the plot when that storey is still empty. This is exactly
 *                 what the F key frames in 2D.
 *   Fit selection the picked elements. Disabled, with the reason said out
 *                 loud, when nothing is picked.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE HEIGHTS, WHICH ONLY 3D READS
 * ════════════════════════════════════════════════════════════════════════════
 * `fitDistanceMm` frames the diagonal of (width, depth, height), so the
 * vertical extent decides how far back the perspective camera stands. Fit-all
 * uses the building's full height (parapet included — a terrace wall is part of
 * what you asked to see). Fit-storey uses that storey's floor-to-floor height.
 * Fit-selection uses the ACTIVE storey's height rather than the building's,
 * because a selected wall is a one-storey-tall object: framing it against a G+2
 * would stand the camera three times too far back for a detail.
 */

import { bbox, type Bbox, type HouseModel, type Polygon, type Storey } from '@garh/model';

// All three deep imports, not barrels: `canvas/core`, `canvas/sun` and
// `pages/project/plan` each re-export React/three components, and this module
// is pure over `HouseModel`. See the same note in `camera.ts`.
import { bboxUnion } from '../canvas/core/coords';
import { buildingExtentOf } from '../canvas/sun/buildingBbox';
import { elementsExtentMm, planExtentMm } from '../../pages/project/plan/planGeometry';
import {
  BUILT_IN_VIEW_IDS,
  type BuiltInViewId,
  type BuiltInViewSpec,
  type ViewExtent,
} from './types';

/**
 * Fallback vertical extent when the model has no storey to ask, in mm. Matches
 * the model's own `DEFAULTS.storeyHeightMm`; used only for an empty project,
 * where the alternative is a zero-height box and a camera at the origin.
 */
const FALLBACK_HEIGHT_MM = 3000;

/** Everything the three built-ins need to resolve. */
export interface BuiltInInput {
  readonly house: HouseModel;
  readonly plotBoundary: Polygon;
  readonly activeStoreyId: string | null;
  readonly selectionIds: readonly string[];
}

/** Floor-to-floor height of a storey, or the fallback. */
function storeyHeightMm(house: HouseModel, storeyId: string | null): number {
  const storey: Storey | undefined =
    storeyId === null ? house.storeys[0] : house.storeys.find((s) => s.id === storeyId);
  return storey === undefined ? FALLBACK_HEIGHT_MM : storey.heightMm;
}

/** The plot outline as a box, or null when no boundary has been set yet. */
function plotBox(plotBoundary: Polygon): Bbox | null {
  return plotBoundary.length >= 3 ? bbox(plotBoundary) : null;
}

/**
 * The whole project — plot and building together.
 *
 * `bboxUnion` tolerates a null on either side, which is what makes the three
 * real cases (plot only, building only, both) one expression instead of a
 * three-branch ladder that has to be read to be believed.
 */
export function fitAllExtent(input: BuiltInInput): ViewExtent | null {
  const building = buildingExtentOf(input.house);
  const box = bboxUnion(plotBox(input.plotBoundary), building?.box ?? null);
  if (box === null) return null;
  return {
    box,
    heightMm: building === null ? FALLBACK_HEIGHT_MM : building.heightMm,
  };
}

/** The active storey's drawing, or the plot while it is still empty. */
export function fitStoreyExtent(input: BuiltInInput): ViewExtent | null {
  const box = planExtentMm(input.house, input.activeStoreyId, input.plotBoundary);
  if (box === null) return null;
  return { box, heightMm: storeyHeightMm(input.house, input.activeStoreyId) };
}

/** The picked elements. Null when nothing is picked, or nothing picked exists. */
export function fitSelectionExtent(input: BuiltInInput): ViewExtent | null {
  const box = elementsExtentMm(input.house, input.selectionIds);
  if (box === null) return null;
  return { box, heightMm: storeyHeightMm(input.house, input.activeStoreyId) };
}

const LABELS: Readonly<Record<BuiltInViewId, string>> = {
  fitAll: 'Fit all',
  fitSelection: 'Fit selection',
  fitStorey: 'Fit storey',
};

/**
 * Why a built-in is unavailable, in words a panel can show on the disabled
 * control. Only reached when the extent came back null, so each reason
 * describes the one thing that can produce that null.
 */
const EMPTY_REASONS: Readonly<Record<BuiltInViewId, string>> = {
  fitAll: 'Nothing drawn yet — add a plot boundary or a wall',
  fitSelection: 'Nothing is selected',
  fitStorey: 'This storey is empty and the plot has no boundary',
};

export function builtInExtent(id: BuiltInViewId, input: BuiltInInput): ViewExtent | null {
  switch (id) {
    case 'fitAll':
      return fitAllExtent(input);
    case 'fitSelection':
      return fitSelectionExtent(input);
    case 'fitStorey':
      return fitStoreyExtent(input);
  }
}

/** All three, resolved against the model as it is right now. */
export function builtInViews(input: BuiltInInput): readonly BuiltInViewSpec[] {
  return BUILT_IN_VIEW_IDS.map((id) => {
    const extent = builtInExtent(id, input);
    return {
      id,
      label: LABELS[id],
      extent,
      reason: extent === null ? EMPTY_REASONS[id] : null,
    };
  });
}
