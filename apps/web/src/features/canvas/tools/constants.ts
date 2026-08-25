/**
 * constants.ts — the numbers the tools agree on.
 *
 * Three categories, and the distinction matters when one of them is wrong:
 *
 *  - **Interaction** (tolerances, rotate steps): tuned, changeable, no
 *    correctness consequence beyond feel.
 *  - **Model invariants** re-stated for the interaction layer (the 115 mm
 *    opening end margin, the ±10 mm stair rise tolerance). These are NOT the
 *    source of truth — `packages/model/src/validate.ts` is, and they are
 *    imported from it rather than retyped, so a change there cannot leave a
 *    tool happily previewing something `fold` will reject.
 *  - **Code bounds** (NBC riser/tread/width/headroom). These MIRROR
 *    `rulepacks/nbc-core.json` and are used only for the live advisory chips a
 *    tool shows while you draw. The authoritative check is the rules engine
 *    running server-side against the same pack; the tool's job is to warn a
 *    second earlier, in the same words. Rule ids are carried on the chips so
 *    the two can be traced to each other.
 */

import { STAIR_RISE_TOLERANCE_MM, WALL_END_MARGIN_MM } from '@garh/model';

import { SNAP_COARSE_MM, SNAP_FINE_MM } from '../../../lib/units';

export { WALL_END_MARGIN_MM, STAIR_RISE_TOLERANCE_MM, SNAP_COARSE_MM, SNAP_FINE_MM };

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

/**
 * How close, in CSS pixels, the pointer must be for an object snap (endpoint,
 * midpoint, plot edge) to beat the grid.
 *
 * Larger than the 6 px pick tolerance on purpose: snapping is forgiving because
 * being one module out is a real error, while picking is precise because
 * selecting the wrong element is merely annoying.
 */
export const SNAP_TOLERANCE_PX = 12;

/** Pixels the pointer must travel before a press becomes a drag, not a click. */
export const DRAG_THRESHOLD_PX = 4;

/** Furniture and stair rotation step, in degrees, for the `X` key. */
export const ROTATE_STEP_DEG = 90;

/**
 * Shortest wall a chain will commit, in mm. Below this the segment is dropped
 * silently rather than sent to be rejected as `WALL_ZERO_LENGTH`: a 3 mm wall
 * is always a slipped click, never an intention.
 */
export const MIN_WALL_LENGTH_MM = SNAP_COARSE_MM;

/** Vertices closer than this are treated as the same point when closing a ring. */
export const RING_CLOSE_TOLERANCE_MM = SNAP_COARSE_MM;

// ---------------------------------------------------------------------------
// Walls
// ---------------------------------------------------------------------------

/** The thickness selector's presets (§F4). "Custom" is any other integer mm. */
export const WALL_THICKNESS_PRESETS: readonly number[] = [230, 200, 150, 115];

/** Thickness the wall tool starts on: a 9" external wall. */
export const DEFAULT_WALL_THICKNESS_MM = 230;

/** Widest wall the selector will accept before it is a data-entry error. */
export const MAX_TOOL_WALL_THICKNESS_MM = 1000;

// ---------------------------------------------------------------------------
// Stairs — NBC bounds (mirrors rulepacks/nbc-core.json, see the header)
// ---------------------------------------------------------------------------

/** `nbc.stair.riser.max` — Part 4, Cl. 4.4.3. */
export const NBC_RISER_MAX_MM = 190;
/** `nbc.stair.tread.min` — Part 4, Cl. 4.4.3. */
export const NBC_TREAD_MIN_MM = 250;
/** `nbc.stair.width.min` — Part 4, Cl. 4.4.2. */
export const NBC_STAIR_WIDTH_MIN_MM = 900;
/** `nbc.stair.headroom.min` — Part 4, Cl. 4.4.5. */
export const NBC_HEADROOM_MIN_MM = 2100;

/** Citation strings, verbatim from the pack, so the chips read identically. */
export const NBC_CITE = {
  riser: 'Part 4, Cl. 4.4.3',
  tread: 'Part 4, Cl. 4.4.3',
  width: 'Part 4, Cl. 4.4.2',
  headroom: 'Part 4, Cl. 4.4.5',
} as const;

/**
 * Comfort rule of thumb: `2 × riser + tread` should land in this band. Not a
 * code requirement — an advisory chip only, and labelled as such.
 */
export const COMFORT_2R_T_MIN_MM = 550;
export const COMFORT_2R_T_MAX_MM = 700;

/** Riser the flight solver aims for before the storey height pushes it around. */
export const PREFERRED_RISER_MM = 165;

/** Search bounds for the riser count. A dwelling flight outside these is a bug. */
export const MIN_RISERS = 8;
export const MAX_RISERS = 40;

// ---------------------------------------------------------------------------
// Balconies
// ---------------------------------------------------------------------------

/** Default railing height (§3 DEFAULTS mirror). */
export const DEFAULT_RAILING_HEIGHT_MM = 1000;

/** Default balcony slab thickness. */
export const DEFAULT_BALCONY_SLAB_MM = 125;

/** Fewest vertices a balcony ring can have. */
export const MIN_BALCONY_VERTICES = 3;

// ---------------------------------------------------------------------------
// Hints — the status-bar copy per phase (§15 tone: plain, warm, no jargon)
// ---------------------------------------------------------------------------

export const HINTS = {
  wallIdle: 'Click to start a wall. Type a length while drawing to set it exactly.',
  wallDrawing: 'Click for the next corner · type a length · Enter to finish · Backspace undoes the last one',
  openingIdle: 'Hover a wall to place it. X flips the swing.',
  openingPreview: 'Click to place · type a distance from the wall start · Esc to cancel',
  stairIdle: 'Click where the first step starts. X turns it, [ and ] change the type.',
  balconyIdle: 'Click the corners of the balcony. Enter closes it.',
  balconyDrawing: 'Click the next corner · Enter to close · Backspace undoes the last one',
  measureIdle: 'Click two points to measure. Keep clicking to chain.',
  measureDrawing: 'Click to add another leg · Enter or Esc to finish',
  furnitureIdle: 'Click to place. X rotates by 90°.',
  furnitureNoItem: 'Pick a piece of furniture first.',
  selectIdle: 'Click to select · drag to move · Shift-click to add · Delete removes',
  selectDragging: 'Drag to move · type an exact distance · Esc to put it back',
  noStorey: 'Add a floor before drawing.',
} as const;
