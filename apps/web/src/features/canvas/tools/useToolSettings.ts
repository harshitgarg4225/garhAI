/**
 * useToolSettings.ts — the tool options bar's state.
 *
 * Wall thickness, opening sizes, stair type, railing kind, the furniture item
 * being placed. None of it is design state: it survives no reload, appears in
 * no op, and changes no document. That is exactly why it is a store of its own
 * rather than fields on the tools — the options bar, the inspector and the
 * keyboard (`X` flipping a swing) all write the same values, and a tool that
 * owned them would lose them the moment you switched tool and came back.
 *
 * Defaults are the §3 Indian residential defaults (`DEFAULTS` in the model
 * core), not numbers invented here: a door is 900 × 2100, a window 1200 × 1200
 * with a 900 sill, an external wall 230 mm. Where the tools need a value the
 * model has no opinion about, the assumption is called out in a comment.
 */

import { create } from 'zustand';

import { DEFAULTS, roundMm } from '@garh/model';

import {
  DEFAULT_BALCONY_SLAB_MM,
  DEFAULT_RAILING_HEIGHT_MM,
  DEFAULT_WALL_THICKNESS_MM,
  MAX_TOOL_WALL_THICKNESS_MM,
  PREFERRED_RISER_MM,
  WALL_THICKNESS_PRESETS,
} from './constants';
import { defaultOpeningParams } from './editOps';
import type { ToolSettings } from './types';

export interface ToolSettingsState extends ToolSettings {
  /** Merge a patch. The only writer a tool ever gets (via `settingsPatch`). */
  patch: (patch: Partial<ToolSettings>) => void;
  /** Back to the defaults — used when a project is closed. */
  reset: () => void;
}

/** The starting values. Exported so the specs can build a context cheaply. */
export const DEFAULT_TOOL_SETTINGS: ToolSettings = {
  wallThicknessMm: DEFAULT_WALL_THICKNESS_MM,
  wallKind: 'external',
  wallLoadBearing: false,
  ortho: true,

  door: defaultOpeningParams('door'),
  window: defaultOpeningParams('window'),
  ventilator: defaultOpeningParams('ventilator'),
  windowVariant: 'window',
  swing: 'in-left',

  stairKind: 'dogleg',
  stairDirection: 'N',
  stairWidthMm: DEFAULTS.stairWidthMm,
  stairPreferredRiserMm: PREFERRED_RISER_MM,

  railingKind: 'ms',
  railingHeightMm: DEFAULT_RAILING_HEIGHT_MM,
  balconySlabThicknessMm: DEFAULT_BALCONY_SLAB_MM,

  // Null, not a guess: the furniture tool refuses to place anything until the
  // catalogue has loaded and an item has been chosen.
  furnitureCatalogId: null,
  furnitureRotationDeg: 0,
};

/** Clamp anything the options bar or a typed value could get wrong. */
function sanitise(patch: Partial<ToolSettings>): Partial<ToolSettings> {
  const out: Partial<ToolSettings> = { ...patch };
  if (out.wallThicknessMm !== undefined) {
    // `roundMm` because this value is copied straight into a `wall.add`
    // payload. Thickness is always positive so the half-up/half-away
    // distinction cannot bite here — using the model's rounder anyway means
    // there is exactly one answer to "how does a float become mm" in the
    // whole canvas, and nobody has to check which one a call site picked.
    out.wallThicknessMm = Math.min(
      MAX_TOOL_WALL_THICKNESS_MM,
      Math.max(1, roundMm(out.wallThicknessMm)),
    );
  }
  if (out.furnitureRotationDeg !== undefined) {
    out.furnitureRotationDeg = ((Math.round(out.furnitureRotationDeg) % 360) + 360) % 360;
  }
  return out;
}

export const useToolSettings = create<ToolSettingsState>()((set) => ({
  ...DEFAULT_TOOL_SETTINGS,
  patch: (patch) => set(sanitise(patch)),
  reset: () => set({ ...DEFAULT_TOOL_SETTINGS }),
}));

/** The presets the thickness selector offers, plus "custom". */
export { WALL_THICKNESS_PRESETS };

/** Snapshot of just the settings, for building a `ToolContext`. */
export function readToolSettings(): ToolSettings {
  const s = useToolSettings.getState();
  return {
    wallThicknessMm: s.wallThicknessMm,
    wallKind: s.wallKind,
    wallLoadBearing: s.wallLoadBearing,
    ortho: s.ortho,
    door: s.door,
    window: s.window,
    ventilator: s.ventilator,
    windowVariant: s.windowVariant,
    swing: s.swing,
    stairKind: s.stairKind,
    stairDirection: s.stairDirection,
    stairWidthMm: s.stairWidthMm,
    stairPreferredRiserMm: s.stairPreferredRiserMm,
    railingKind: s.railingKind,
    railingHeightMm: s.railingHeightMm,
    balconySlabThicknessMm: s.balconySlabThicknessMm,
    furnitureCatalogId: s.furnitureCatalogId,
    furnitureRotationDeg: s.furnitureRotationDeg,
  };
}
