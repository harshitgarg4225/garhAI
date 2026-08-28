/**
 * geometry.test.ts — is the browser port the same generator as the Python?
 *
 * Three layers, weakest last:
 *
 *  1. GOLDEN PARITY. Three fixtures captured from the real
 *     `hatch_families()` in `services/drawings/render/hatch_patterns.py`
 *     (`diagonal` rotated to 45 deg, `brick`, `earth`) — segments, dash
 *     cycles, dash offsets, phases and all. If the port and the renderer ever
 *     disagree about a millimetre, this is where it shows. The fixtures were
 *     produced by running that function, not by reading it; if Python's
 *     algorithm changes they go stale, and stale is a red test, which is the
 *     intent.
 *
 *  2. THE THREE SHIPPED DEFECTS. Earth must not equal cross; the authored
 *     spacing must be the spacing that comes out (the 31x-too-dense bug); the
 *     pattern's own definition angle must not be added twice (the 45→90 bug).
 *
 *  3. INVARIANTS ACROSS ALL FIFTEEN, with expectations read from the PYTHON
 *     defs via `pythonDefs.ts` — never from `patterns.ts`, which is the table
 *     the code under test reads. An assertion sourced from its own subject is
 *     the test that cannot fail.
 */

import { describe, expect, it } from 'vitest';

import {
  baseAngleDeg,
  baseSpacing,
  hatchFamilies,
  perpSpacing,
  type BBox,
  type HatchFamily,
} from './geometry';
import { HATCH_PATTERN_KEYS, hatchPattern, type HatchPatternKey } from './patterns';
import { readPythonHatchDefs, type PythonHatchDef } from './pythonDefs';

const PY: readonly PythonHatchDef[] = readPythonHatchDefs();
const pyDef = (key: string): PythonHatchDef => {
  const found = PY.find((d) => d.key === key);
  if (found === undefined) throw new Error(`no python def for ${key}`);
  return found;
};

/** Every pattern that has line geometry — i.e. everything but `solid`. */
const DRAWN: readonly HatchPatternKey[] = HATCH_PATTERN_KEYS.filter(
  (key) => hatchPattern(key).lines.length > 0,
);

function shape(families: readonly HatchFamily[]): unknown {
  return families.map((family) => ({
    segments: family.segments.map(([a, b]) => [
      [a[0], a[1]],
      [b[0], b[1]],
    ]),
    dashes: [...family.dashes],
    dashOffset: family.dashOffset,
    dotted: family.dotted,
  }));
}

const segmentCount = (families: readonly HatchFamily[]): number =>
  families.reduce((sum, family) => sum + family.segments.length, 0);

/** Direction of a segment in degrees, folded to [0, 180) — a line has no sense. */
function direction(family: HatchFamily, index = 0): number {
  const segment = family.segments[index];
  if (segment === undefined) throw new Error('empty family');
  const [[ax, ay], [bx, by]] = segment;
  const deg = (Math.atan2(by - ay, bx - ax) * 180) / Math.PI;
  return ((deg % 180) + 180) % 180;
}

// ── 1. Golden parity with the Python generator ──────────────────────────────
// Captured with:
//   hatch_families("diagonal", spacing=250, angle_deg=45, bbox=(0,0,2000,1500))
//   hatch_families("brick",    spacing=250, angle_deg=0,  bbox=(0,0,2000,1500))
//   hatch_families("earth",    spacing=300, angle_deg=0,  bbox=(0,0,1200,1200))
const GOLDEN_DIAGONAL_45: unknown = JSON.parse(
  '[{"segments": [[[1061, -1061], [2811, 689]], [[884, -884], [2634, 866]], [[707, -707], [2457, 1043]], [[530, -530], [2280, 1220]], [[354, -354], [2104, 1396]], [[177, -177], [1927, 1573]], [[0, 0], [1750, 1750]], [[-177, 177], [1573, 1927]], [[-354, 354], [1396, 2104]], [[-530, 530], [1220, 2280]], [[-707, 707], [1043, 2457]], [[-884, 884], [866, 2634]]], "dashes": [], "dashOffset": 0, "dotted": false}]',
);
const GOLDEN_BRICK: unknown = JSON.parse(
  '[{"segments": [[[0, 0], [2000, 0]], [[0, 250], [2000, 250]], [[0, 500], [2000, 500]], [[0, 750], [2000, 750]], [[0, 1000], [2000, 1000]], [[0, 1250], [2000, 1250]], [[0, 1500], [2000, 1500]]], "dashes": [], "dashOffset": 0, "dotted": false}, {"segments": [[[2000, 0], [2000, 1500]], [[1500, 0], [1500, 1500]], [[1000, 0], [1000, 1500]], [[500, 0], [500, 1500]], [[0, 0], [0, 1500]], [[-500, 0], [-500, 1500]]], "dashes": [250, 250], "dashOffset": 0, "dotted": false}, {"segments": [[[2250, 0], [2250, 1500]], [[1750, 0], [1750, 1500]], [[1250, 0], [1250, 1500]], [[750, 0], [750, 1500]], [[250, 0], [250, 1500]], [[-250, 0], [-250, 1500]]], "dashes": [250, 250], "dashOffset": 0, "dotted": false}]',
);
const GOLDEN_EARTH: unknown = JSON.parse(
  '[{"segments": [[[0, 0], [1200, 0]], [[0, 600], [1200, 600]], [[0, 1200], [1200, 1200]]], "dashes": [300, 300], "dashOffset": 0, "dotted": false}, {"segments": [[[0, 300], [1200, 300]], [[0, 900], [1200, 900]]], "dashes": [300, 300], "dashOffset": 300, "dotted": false}, {"segments": [[[0, 113], [1200, 113]], [[0, 713], [1200, 713]], [[0, 1313], [1200, 1313]]], "dashes": [300, 300], "dashOffset": 0, "dotted": false}, {"segments": [[[0, -188], [1200, -188]], [[0, 413], [1200, 413]], [[0, 1013], [1200, 1013]]], "dashes": [300, 300], "dashOffset": 300, "dotted": false}, {"segments": [[[0, 225], [1200, 225]], [[0, 825], [1200, 825]], [[0, 1425], [1200, 1425]]], "dashes": [300, 300], "dashOffset": 0, "dotted": false}, {"segments": [[[0, -75], [1200, -75]], [[0, 525], [1200, 525]], [[0, 1125], [1200, 1125]]], "dashes": [300, 300], "dashOffset": 300, "dotted": false}, {"segments": [[[-263, 0], [-262, 1200]]], "dashes": [300, 300], "dashOffset": 37, "dotted": false}, {"segments": [[[938, 0], [938, 1200]], [[338, 0], [338, 1200]]], "dashes": [300, 300], "dashOffset": 38, "dotted": false}, {"segments": [[[37, 0], [38, 1200]]], "dashes": [300, 300], "dashOffset": 337, "dotted": false}, {"segments": [[[1238, 0], [1238, 1200]], [[638, 0], [638, 1200]]], "dashes": [300, 300], "dashOffset": 338, "dotted": false}, {"segments": [[[-150, 0], [-150, 1200]]], "dashes": [300, 300], "dashOffset": 37, "dotted": false}, {"segments": [[[1050, 0], [1050, 1200]], [[450, 0], [450, 1200]]], "dashes": [300, 300], "dashOffset": 38, "dotted": false}, {"segments": [[[150, 0], [150, 1200]]], "dashes": [300, 300], "dashOffset": 337, "dotted": false}, {"segments": [[[1350, 0], [1350, 1200]], [[750, 0], [750, 1200]]], "dashes": [300, 300], "dashOffset": 338, "dotted": false}, {"segments": [[[-38, 0], [-37, 1200]]], "dashes": [300, 300], "dashOffset": 37, "dotted": false}, {"segments": [[[1163, 0], [1163, 1200]], [[563, 0], [563, 1200]]], "dashes": [300, 300], "dashOffset": 38, "dotted": false}, {"segments": [[[263, 0], [263, 1200]]], "dashes": [300, 300], "dashOffset": 337, "dotted": false}, {"segments": [[[1463, 0], [1463, 1200]], [[863, 0], [863, 1200]]], "dashes": [300, 300], "dashOffset": 338, "dotted": false}]',
);

describe('the port reproduces the Python generator exactly', () => {
  it('diagonal at 45 deg, segment for segment', () => {
    expect(
      shape(hatchFamilies('diagonal', { spacing: 250, angleDeg: 45, bbox: [0, 0, 2000, 1500] })),
    ).toEqual(GOLDEN_DIAGONAL_45);
  });

  it('brick — course lines plus the two staggered joint families', () => {
    expect(
      shape(hatchFamilies('brick', { spacing: 250, angleDeg: 0, bbox: [0, 0, 2000, 1500] })),
    ).toEqual(GOLDEN_BRICK);
  });

  it('earth — eighteen phase groups, dash offsets included', () => {
    // Phase grouping is where a `%` that forgets Python's non-negative modulo
    // shows up: half these offsets would come back negative.
    expect(
      shape(hatchFamilies('earth', { spacing: 300, angleDeg: 0, bbox: [0, 0, 1200, 1200] })),
    ).toEqual(GOLDEN_EARTH);
    expect(
      hatchFamilies('earth', { spacing: 300, angleDeg: 0, bbox: [0, 0, 1200, 1200] }).every(
        (f) => f.dashOffset >= 0,
      ),
    ).toBe(true);
  });
});

// ── 2. The three defects this library was written to end ────────────────────
describe('the defects this pattern library exists to prevent', () => {
  it('earth and cross are different geometry', () => {
    const box: BBox = [0, 0, 2000, 2000];
    const earth = hatchFamilies('earth', { spacing: 200, angleDeg: 0, bbox: box });
    const cross = hatchFamilies('cross', { spacing: 200, angleDeg: 0, bbox: box });
    expect(shape(earth)).not.toEqual(shape(cross));
    // Not just "different numbers": earth is dashed, cross is continuous.
    expect(earth.some((f) => f.dashes.length > 0)).toBe(true);
    expect(cross.every((f) => f.dashes.length === 0)).toBe(true);
  });

  it('the authored spacing IS the spacing that comes out', () => {
    // The 31x-too-dense defect: a pattern's intrinsic spacing was used as if it
    // were the authored one. Measured on every pattern whose FIRST family has a
    // direction no other family shares — for those, and only those, every line
    // running that way came from family 0, so the gaps between them ARE the
    // authored spacing. (Family 0 is rotated to 0 deg by the call below, so the
    // gap is the difference in y.)
    const box: BBox = [0, 0, 4000, 4000];
    const measured: string[] = [];
    for (const key of DRAWN) {
      const definition = pyDef(key);
      const first = definition.lines[0];
      if (first === undefined) continue;
      const fold = (deg: number): number => ((deg % 180) + 180) % 180;
      const sharing = definition.lines.filter(
        (line) => Math.abs(fold(line.angleDeg) - fold(first.angleDeg)) < 1e-9,
      );
      if (sharing.length !== 1) continue;

      for (const spacing of [120, 250, 500]) {
        const families = hatchFamilies(key, { spacing, angleDeg: 0, bbox: box });
        const horizontal = families.filter((family) => {
          const deg = direction(family);
          return deg < 0.5 || deg > 179.5;
        });
        expect(horizontal.length, `${key} drew nothing along its first family`).toBeGreaterThan(0);
        const offsets = [
          ...new Set(horizontal.flatMap((family) => family.segments.map((s) => s[0][1]))),
        ].sort((a, b) => a - b);
        expect(offsets.length, `${key} drew one line, so nothing to measure`).toBeGreaterThan(1);
        for (let i = 1; i < offsets.length; i += 1) {
          const gap = Math.abs((offsets[i] ?? 0) - (offsets[i - 1] ?? 0));
          // +-1 mm: the endpoints are rounded to integer millimetres.
          expect(
            Math.abs(gap - spacing),
            `${key} @ ${String(spacing)} spaced ${String(gap)}`,
          ).toBeLessThanOrEqual(1);
        }
      }
      measured.push(key);
    }
    // Guard the guard. If the filter above ever selects nothing — or only the
    // patterns whose offset happens to be perpendicular already, which cannot
    // detect a wrongly measured spacing — this test would pass while measuring
    // nothing that matters.
    expect(measured).toEqual([
      'diagonal',
      'cross',
      'brick',
      'concrete',
      'sand',
      'timber',
      'tile',
      'grass',
    ]);
  });

  it('a pattern defined at an angle does not get that angle applied twice', () => {
    // ANSI31 is defined at 45 deg. Asking for 45 must give 45, not 90.
    expect(
      direction(
        hatchFamilies('diagonal', { spacing: 250, angleDeg: 45, bbox: [0, 0, 2000, 2000] })[0]!,
      ),
    ).toBeCloseTo(45, 1);
    expect(
      direction(
        hatchFamilies('diagonal', { spacing: 250, angleDeg: 0, bbox: [0, 0, 2000, 2000] })[0]!,
      ),
    ).toBeCloseTo(0, 1);
    expect(
      direction(
        hatchFamilies('diagonal', { spacing: 250, angleDeg: 90, bbox: [0, 0, 2000, 2000] })[0]!,
      ),
    ).toBeCloseTo(90, 1);
    // BRICK is defined at 0 deg, so it is the control: 45 means 45 for it too.
    expect(
      direction(
        hatchFamilies('brick', { spacing: 250, angleDeg: 45, bbox: [0, 0, 2000, 2000] })[0]!,
      ),
    ).toBeCloseTo(45, 1);
  });
});

// ── 3. Invariants over all fifteen, expected values from the Python ─────────
describe('every pattern in the library', () => {
  it('generates geometry, and only `solid` generates none', () => {
    const box: BBox = [0, 0, 4000, 4000];
    expect(hatchFamilies('solid', { spacing: 250, angleDeg: 0, bbox: box })).toEqual([]);
    for (const key of DRAWN) {
      const families = hatchFamilies(key, { spacing: 250, angleDeg: 0, bbox: box });
      expect(families.length, `${key} produced no families`).toBeGreaterThan(0);
      expect(segmentCount(families), `${key} produced no segments`).toBeGreaterThan(0);
    }
    expect(DRAWN).toHaveLength(14);
  });

  it('draws only at the directions the Python definition declares', () => {
    const box: BBox = [0, 0, 6000, 6000];
    for (const key of DRAWN) {
      const definition = pyDef(key);
      const requested = 30;
      const rotation = requested - (definition.lines[0]?.angleDeg ?? 0);
      const expected = new Set(
        definition.lines.map(
          (line) => Math.round(((((line.angleDeg + rotation) % 180) + 180) % 180) * 10) / 10,
        ),
      );
      const families = hatchFamilies(key, { spacing: 300, angleDeg: requested, bbox: box });
      for (const family of families) {
        const got = direction(family);
        const near = [...expected].some(
          (angle) => Math.abs(angle - got) < 0.5 || Math.abs(Math.abs(angle - got) - 180) < 0.5,
        );
        expect(
          near,
          `${key}: drew a family at ${String(got)}, defined angles ${[...expected].join(', ')}`,
        ).toBe(true);
      }
    }
  });

  it('dashes appear exactly where the Python definition has them', () => {
    const box: BBox = [0, 0, 4000, 4000];
    for (const key of DRAWN) {
      const definition = pyDef(key);
      const anyDashed = definition.lines.some((line) => line.dashes.length > 0);
      const allDashed = definition.lines.every((line) => line.dashes.length > 0);
      const families = hatchFamilies(key, { spacing: 300, angleDeg: 0, bbox: box });
      expect(
        families.some((f) => f.dashes.length > 0),
        `${key}`,
      ).toBe(anyDashed);
      if (allDashed)
        expect(
          families.every((f) => f.dashes.length > 0),
          `${key}`,
        ).toBe(true);
      for (const family of families) {
        // SVG dash lengths are positive; a negative one would draw nothing and
        // is how an ACAD gap gets mistranslated.
        for (const dash of family.dashes) expect(dash).toBeGreaterThan(0);
        expect(family.dashOffset).toBeGreaterThanOrEqual(0);
      }
    }
  });

  it('a zero-length ACAD dash becomes a dot, and says so', () => {
    // AR-SAND is stipple: every drawn entry in its dash lists is 0.
    const sand = pyDef('sand');
    expect(
      sand.lines.every((line) => line.dashes.filter((_, i) => i % 2 === 0).every((d) => d === 0)),
    ).toBe(true);
    const families = hatchFamilies('sand', { spacing: 300, angleDeg: 0, bbox: [0, 0, 4000, 4000] });
    expect(families.length).toBeGreaterThan(0);
    expect(families.every((f) => f.dotted)).toBe(true);
    // A dot is drawn as a short dash, never as a zero-length one (invisible).
    for (const family of families) expect(family.dashes[0]).toBeGreaterThan(0);
    // And a pattern with real dashes is NOT flagged dotted.
    expect(
      hatchFamilies('brick', { spacing: 300, angleDeg: 0, bbox: [0, 0, 4000, 4000] }).some(
        (f) => f.dotted,
      ),
    ).toBe(false);
  });

  it('halving the spacing roughly doubles the line count', () => {
    const box: BBox = [0, 0, 4000, 4000];
    for (const key of DRAWN) {
      const coarse = segmentCount(hatchFamilies(key, { spacing: 400, angleDeg: 0, bbox: box }));
      const fine = segmentCount(hatchFamilies(key, { spacing: 200, angleDeg: 0, bbox: box }));
      // Not exact: each family rounds its own first/last line outward, and the
      // phase split changes. The point is the ORDER — a scale applied to the
      // wrong quantity misses by 31x, not by 10%.
      expect(fine / coarse, `${key}`).toBeGreaterThan(1.5);
      expect(fine / coarse, `${key}`).toBeLessThan(2.6);
    }
  });

  it('the line cap fires instead of emitting unbounded geometry', () => {
    const families = hatchFamilies('diagonal', {
      spacing: 1,
      angleDeg: 0,
      bbox: [0, 0, 1_000_000, 1_000_000],
      maxLines: 25,
    });
    expect(segmentCount(families)).toBe(25);
    // …and the cap is not doing the work in normal use.
    expect(
      segmentCount(
        hatchFamilies('diagonal', { spacing: 250, angleDeg: 0, bbox: [0, 0, 4000, 4000] }),
      ),
    ).toBeLessThan(25);
  });

  it('rejects nothing silently: a non-positive spacing draws nothing', () => {
    expect(hatchFamilies('brick', { spacing: 0, angleDeg: 0, bbox: [0, 0, 1000, 1000] })).toEqual(
      [],
    );
    expect(hatchFamilies('brick', { spacing: -5, angleDeg: 0, bbox: [0, 0, 1000, 1000] })).toEqual(
      [],
    );
  });
});

describe('the pattern metrics the fit and the renderer both read', () => {
  it('perpSpacing is the normal component of the offset, not its length', () => {
    // ANSI31: offset (-2.245, 2.245) at 45 deg is 3.175 of separation, and
    // 3.175 is what ACAD means by ANSI31's spacing. Reading the vector's
    // length (3.175 * sqrt(2) = 4.49) is the 41%-coarse version of the bug.
    const ansi31 = pyDef('diagonal').lines[0]!;
    expect(perpSpacing(ansi31)).toBeCloseTo(3.175, 6);
    expect(Math.hypot(ansi31.offset[0], ansi31.offset[1])).toBeCloseTo(3.1749, 3);
  });

  it('base spacing and base angle come from the first family, for every pattern', () => {
    for (const key of DRAWN) {
      const python = pyDef(key);
      const first = python.lines[0]!;
      expect(baseAngleDeg(hatchPattern(key))).toBe(first.angleDeg);
      expect(baseSpacing(hatchPattern(key))).toBeCloseTo(perpSpacing(first), 9);
    }
    expect(baseSpacing(hatchPattern('solid'))).toBe(0);
  });
});
