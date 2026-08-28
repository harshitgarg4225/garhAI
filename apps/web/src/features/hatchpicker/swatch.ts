/**
 * swatch.ts — how big to draw a pattern so that a 40-pixel square says which
 * pattern it is.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE PROBLEM A SWATCH HAS THAT A SHEET DOES NOT
 * ════════════════════════════════════════════════════════════════════════════
 * On a sheet the author chooses the spacing (2.5 paper mm, `style.py`), and
 * every pattern obeys it. In a picker there is no author yet, and the fifteen
 * patterns are not remotely the same size: ANSI31's families sit 3.175 units
 * apart while AR-CONC's first family sits 149.8 apart and its dash cycles run
 * to 524. Handing them all one spacing produces a picker where `diagonal` is a
 * grey smear and `concrete` is an empty box — a swatch that is wrong about the
 * pattern, which the task rightly calls worse than a text list.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE FIT, AND WHY IT IS DERIVED RATHER THAN TUNED
 * ════════════════════════════════════════════════════════════════════════════
 * A per-pattern table of hand-picked preview scales would be a sixteenth thing
 * to keep in step with the Python — the exact drift this feature is under
 * orders to avoid. So the scale is COMPUTED from each pattern's own
 * definition: pick the scale at which the densest line family shows about
 * `SWATCH_TARGET_LINES` lines across the box. Nine is the number that keeps
 * `diagonal` legible at 40 px and stops `steel`'s eight-family bundle from
 * merging into one grey block.
 *
 * A pattern added to the library on Monday gets a sensible swatch on Monday,
 * with nobody editing this file. `swatch.test.ts` checks the outcome that
 * actually matters — every pattern draws real, visible ink inside the box —
 * rather than checking the arithmetic against itself.
 */

import {
  baseAngleDeg,
  baseSpacing,
  hatchFamilies,
  perpSpacing,
  type BBox,
  type HatchFamily,
} from './geometry';
import { hatchPattern, isSolidPattern, type HatchPatternKey } from './patterns';
import { roundHalfAwayFromZero } from '../../lib/units';

/**
 * The swatch's coordinate space: a square of this many units, rendered
 * through an SVG `viewBox` at whatever pixel size the caller wants. Large
 * enough that `hatchFamilies`' rounding to integer millimetres costs no
 * visible accuracy at any display size.
 */
export const SWATCH_UNITS = 1024;

/**
 * Lines the busiest DIRECTION shows across the box.
 *
 * Per direction and not per family, because several families routinely share
 * one direction and interleave: BRSTONE has six families running at 0 deg,
 * and fitting each of them to nine lines fills the tile with sixty-five
 * horizontal lines — a grey block, not a stone wall. What a reader sees is the
 * total in a direction, so that is what is fitted.
 */
export const SWATCH_TARGET_LINES = 12;

export interface SwatchOptions {
  readonly units?: number;
  readonly targetLines?: number;
  /** Defaults to the pattern's own definition angle, i.e. no rotation. */
  readonly angleDeg?: number;
}

export interface SwatchGeometry {
  readonly key: HatchPatternKey;
  /** `solid` is a fill: no families, the caller paints the box. */
  readonly solid: boolean;
  /** Side of the square coordinate space — the `viewBox`. */
  readonly units: number;
  /** Angle the first family is drawn at. */
  readonly angleDeg: number;
  /** The spacing handed to `hatchFamilies`, in swatch units. */
  readonly spacing: number;
  readonly families: readonly HatchFamily[];
}

/**
 * The spacing at which this pattern reads best in a `units`-sized box.
 *
 * For each family: the box's extent measured along that family's NORMAL is
 * `units * (|nx| + |ny|)` (a square's support width), and the family will show
 * `extent / (perpSpacing * scale)` lines. Sum that per direction, solve so the
 * busiest direction lands on `targetLines`, then express the answer as the
 * FIRST family's spacing — which is what `hatchFamilies` takes.
 */
export function swatchSpacing(
  key: HatchPatternKey,
  { units = SWATCH_UNITS, targetLines = SWATCH_TARGET_LINES, angleDeg }: SwatchOptions = {},
): number {
  const definition = hatchPattern(key);
  const base = baseSpacing(definition);
  if (definition.lines.length === 0 || base <= 0) return 0;
  const rotation =
    ((angleDeg ?? baseAngleDeg(definition)) - baseAngleDeg(definition)) * (Math.PI / 180);

  // Lines per direction, at scale 1. Keyed on the UNROTATED angle folded to
  // [0, 180): rotation moves every family by the same amount, so it cannot
  // change which families share a direction.
  const perDirection = new Map<number, number>();
  for (const line of definition.lines) {
    const perp = perpSpacing(line);
    // A family whose offset runs along its own lines draws nothing at all
    // (`hatchFamilies` skips it); letting it into the fit would divide by zero
    // and hand every other family an infinitely fine spacing.
    if (perp <= 1e-9) continue;
    const theta = (line.angleDeg * Math.PI) / 180 + rotation;
    const support = units * (Math.abs(-Math.sin(theta)) + Math.abs(Math.cos(theta)));
    const key180 = Math.round((((line.angleDeg % 180) + 180) % 180) * 1000) / 1000;
    perDirection.set(key180, (perDirection.get(key180) ?? 0) + support / perp);
  }
  const busiest = Math.max(0, ...perDirection.values());
  if (busiest <= 0) return 0;
  // A spacing of 0 would make `hatchFamilies` return nothing, so the floor is
  // one unit: a pathologically dense pattern draws dense rather than blank.
  return Math.max(1, roundHalfAwayFromZero((busiest / targetLines) * base));
}

/** Everything `HatchSwatch` needs to draw one pattern. Pure. */
export function swatchGeometry(key: HatchPatternKey, options: SwatchOptions = {}): SwatchGeometry {
  const units = options.units ?? SWATCH_UNITS;
  const definition = hatchPattern(key);
  const angleDeg = options.angleDeg ?? baseAngleDeg(definition);
  if (isSolidPattern(key)) {
    return { key, solid: true, units, angleDeg, spacing: 0, families: [] };
  }
  const spacing = swatchSpacing(key, options);
  const bbox: BBox = [0, 0, units, units];
  return {
    key,
    solid: false,
    units,
    angleDeg,
    spacing,
    families: hatchFamilies(key, { spacing, angleDeg, bbox }),
  };
}

/** The `d` attribute for one family: one `M … L …` per line. */
export function familyPath(family: HatchFamily): string {
  return family.segments
    .map(([a, b]) => `M ${String(a[0])} ${String(a[1])} L ${String(b[0])} ${String(b[1])}`)
    .join(' ');
}
