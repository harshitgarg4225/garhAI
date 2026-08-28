/**
 * types.ts — what a saved view IS.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THE CAMERA IS A DISCRIMINATED UNION AND NOT ONE FLAT RECORD
 * ════════════════════════════════════════════════════════════════════════════
 * `ViewportController` keeps `view2d` and `orbit` alive at the same time — that
 * is its stated design, and it is what makes Tab a projection change rather
 * than a rebuild. But the two describe the camera in different vocabularies: an
 * orthographic plan is a centre and a `mmPerPx`, a perspective view is a
 * target, a distance and two angles. There is no shared subset, and no honest
 * conversion between them (see the mode-mismatch note in `restore.ts`).
 *
 * So a saved view carries the projection it was saved in, and the union tag is
 * what every consumer branches on. A single flat record with six optional
 * fields would compile, and would let a 3D view be restored into an
 * orthographic frustum with `undefined` angles — a camera at the origin looking
 * nowhere, and no type error anywhere.
 *
 * The two payload shapes are deliberately STRUCTURALLY IDENTICAL to `View2D`
 * and `Orbit3D` from the canvas core (minus the tag). Capture is then a field
 * copy and restore is a field copy back, with nothing reconstructed, derived or
 * re-rounded in between. That is the whole reason a round trip can be exact
 * rather than approximate — see the exactness note in `camera.ts`.
 */

import type { Bbox } from '@garh/model';

import type { CanvasMode, Orbit3D, View2D } from '../canvas/core';

/** The orthographic plan camera, exactly as `ViewportController` holds it. */
export interface Saved2dCamera extends View2D {
  readonly mode: '2d';
}

/** The perspective camera, exactly as `ViewportController` holds it. */
export interface Saved3dCamera extends Orbit3D {
  readonly mode: '3d';
}

/**
 * Everything that determines what is on screen, in one projection.
 *
 * Note what is NOT here: viewport size, plane elevation, the active storey, the
 * fit height. Those are properties of the SESSION, not of the view — restoring
 * a view saved on a 27" monitor onto a laptop should show the same place at the
 * same scale, not letterbox it, and restoring a view must never move the
 * architect to a different storey behind their back.
 */
export type SavedCamera = Saved2dCamera | Saved3dCamera;

/** A view the architect named and kept. */
export interface NamedView {
  readonly id: string;
  readonly name: string;
  readonly camera: SavedCamera;
  /** Epoch ms. Display only — order is the array's, not this. */
  readonly createdAt: number;
}

/**
 * The views computed from the model instead of stored.
 *
 * They are not saved views with a flag: a saved view is a fixed camera, and
 * these are a QUESTION asked of the current model ("where is the selection
 * now?"). Storing the answer would be storing a camera that silently stops
 * meaning what its name says the moment a wall moves.
 */
export const BUILT_IN_VIEW_IDS = ['fitAll', 'fitSelection', 'fitStorey'] as const;

export type BuiltInViewId = (typeof BUILT_IN_VIEW_IDS)[number];

/** What a built-in frames: a plan-space box plus the vertical extent 3D needs. */
export interface ViewExtent {
  readonly box: Bbox;
  /**
   * Vertical extent in mm, for the perspective fit. `fitDistanceMm` uses the
   * diagonal of (width, depth, height), so a wall framed with a zero height
   * would put the camera inside it.
   */
  readonly heightMm: number;
}

/**
 * One built-in, resolved against the model as it is right now.
 *
 * `extent === null` means there is nothing to frame, and `reason` says why in
 * words the panel shows on the disabled control. A button that is enabled and
 * does nothing is the failure this repo has already shipped once; a button that
 * is disabled and says "nothing is selected" is the honest version.
 */
export interface BuiltInViewSpec {
  readonly id: BuiltInViewId;
  readonly label: string;
  readonly extent: ViewExtent | null;
  readonly reason: string | null;
}

/**
 * What a restore actually did.
 *
 * Returned rather than swallowed so the caller — and the specs — can tell a
 * same-mode animated restore from a cross-mode instant one, and so the panel
 * can report "switched to 2D" instead of the camera appearing to jump for no
 * stated reason.
 */
export interface RestoreOutcome {
  /**
   * The camera this restore lands on — exactly, to the last bit, for any
   * camera the controller accepts unchanged (`isStorableCamera`, which is
   * every camera this feature stores or computes). On the animated path it is
   * where the flight ends, not where the camera is at the moment this record
   * is handed back.
   */
  readonly camera: SavedCamera;
  /**
   * The projection the app was asked to switch to, or null when the live mode
   * already matched. See the mode-mismatch decision in `restore.ts`.
   */
  readonly modeRequested: CanvasMode | null;
  /** False when reduced motion is on, when crossing modes, or when told to. */
  readonly animated: boolean;
  /** Stops an animated restore early. A no-op once it has landed. */
  readonly cancel: () => void;
}

/** Who and what a saved list belongs to. Both halves are in the storage key. */
export interface ViewsScope {
  readonly userId: string;
  readonly projectId: string;
}

export type { CanvasMode };
