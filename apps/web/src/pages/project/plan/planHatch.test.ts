/**
 * A bound wall hatches on the plan; an unbound one does not.
 *
 * The lines come from the same generator the sheet uses and are clipped to
 * the wall, so the assertions are geometric: every emitted segment lies
 * inside the hatched wall's own quad, none lie inside the unbound wall, and
 * taking the binding away takes the lines away — the negative control that
 * proves the gate can go red.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  FIXTURE_IDS,
  applyGroup,
  fixedId,
  makeTwoRoomPlanWithOpenings,
  type Op,
  type ProjectDoc,
} from '@garh/model';

import type { HatchOverrides } from '../../../features/hatchpicker';
import { hatchTargetKey } from '../../../features/hatchpicker';
import type { MaterialItem } from '../../../lib/schemas';
import { wallQuadF, type PtF } from './planGeometry';
import {
  CANVAS_HATCH_SCALE_DENOMINATOR,
  CANVAS_HATCH_SPACING_MM,
  buildWallHatch,
  clipToConvexQuad,
  pushDashed,
  wallHatchPattern,
} from './planHatch';

/** `services/drawings/projection/style.py`, from this file. */
const STYLE_PY_RELATIVE = '../../../../../../services/drawings/projection/style.py';

const STOREY = FIXTURE_IDS.groundStorey;
const SOUTH = FIXTURE_IDS.wallSouth;
const SPINE = FIXTURE_IDS.wallSpine;

const BRICK: MaterialItem = {
  id: 'brick-exposed-wirecut',
  name: 'Exposed wire-cut brick',
  category: 'wall',
  colorHex: '#B5573A',
  textureUrl: null,
  surfaceGroups: ['wall.exterior'],
};
const CATALOG = new Map([[BRICK.id, BRICK]]);
const NO_OVERRIDES: HatchOverrides = new Map();

/** The two-room plan with the SOUTH wall's material bound to exposed brick. */
function boundDoc(): ProjectDoc {
  const op: Op = {
    type: 'material.assign',
    payload: {
      id: fixedId('material', 'M9'),
      target: { group: 'external_wall', storeyId: null, elementId: SOUTH },
      materialId: BRICK.id,
    },
  };
  return applyGroup(makeTwoRoomPlanWithOpenings(), [op]).model;
}

/** Point-in-convex-polygon, with a small tolerance for the clipped ends. */
function insideQuad(p: PtF, quad: readonly PtF[], tolerance = 0.5): boolean {
  let sign = 0;
  for (let i = 0; i < quad.length; i += 1) {
    const a = quad[i] as PtF;
    const b = quad[(i + 1) % quad.length] as PtF;
    const cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    const dist = cross / len;
    if (Math.abs(dist) <= tolerance) continue;
    const s = Math.sign(dist);
    if (sign === 0) sign = s;
    else if (s !== sign) return false;
  }
  return true;
}

describe('which walls hatch', () => {
  it('a wall bound to a material hatches with that material’s pattern', () => {
    const doc = boundDoc();
    const south = doc.house.walls.find((w) => w.id === SOUTH);
    if (south === undefined) throw new Error('fixture');
    expect(wallHatchPattern(doc.house.materials, CATALOG, NO_OVERRIDES, south)).toBe('brick');
  });

  it('an unbound wall keeps the flat poché (null), even though the sheet defaults it', () => {
    const doc = boundDoc();
    const spine = doc.house.walls.find((w) => w.id === SPINE);
    if (spine === undefined) throw new Error('fixture');
    expect(wallHatchPattern(doc.house.materials, CATALOG, NO_OVERRIDES, spine)).toBeNull();
  });

  it('a hand-picked override hatches too, and a solid override does not', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const spine = doc.house.walls.find((w) => w.id === SPINE);
    if (spine === undefined) throw new Error('fixture');
    const target = { group: 'internal_wall' as const, storeyId: null, elementId: null };
    const stone: HatchOverrides = new Map([[hatchTargetKey(target), { target, pattern: 'stone' }]]);
    expect(wallHatchPattern(doc.house.materials, CATALOG, stone, spine)).toBe('stone');
    const solid: HatchOverrides = new Map([[hatchTargetKey(target), { target, pattern: 'solid' }]]);
    expect(wallHatchPattern(doc.house.materials, CATALOG, solid, spine)).toBeNull();
  });
});

describe('buildWallHatch', () => {
  it('draws lines inside the bound wall only, and none in the unbound one', () => {
    const doc = boundDoc();
    const hatch = buildWallHatch({
      house: doc.house,
      storeyId: STOREY,
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
    });
    expect([...hatch.wallIds]).toEqual([SOUTH]);
    expect(hatch.patternByWall.get(SOUTH)).toBe('brick');
    expect(hatch.segments.length).toBeGreaterThan(20);

    const south = doc.house.walls.find((w) => w.id === SOUTH);
    const spine = doc.house.walls.find((w) => w.id === SPINE);
    if (south === undefined || spine === undefined) throw new Error('fixture');
    const southQuad = wallQuadF(south);
    const spineQuad = wallQuadF(spine);
    if (southQuad === null || spineQuad === null) throw new Error('fixture');
    for (const [a, b] of hatch.segments) {
      expect(insideQuad(a, southQuad)).toBe(true);
      expect(insideQuad(b, southQuad)).toBe(true);
    }
    // The spine's own interior (well away from the south wall) holds no line.
    const mid = { x: 3000, y: 2000 };
    expect(
      hatch.segments.some(([a, b]) => insideQuad(a, spineQuad) && a.y > 200 && b.y > 200),
    ).toBe(false);
    expect(insideQuad(mid, spineQuad)).toBe(true);
  });

  it('leaves the door opening un-hatched', () => {
    const doc = boundDoc();
    const door = doc.house.openings.find((o) => o.wallId === SOUTH);
    if (door === undefined) throw new Error('fixture');
    const hatch = buildWallHatch({
      house: doc.house,
      storeyId: STOREY,
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
    });
    const gapStart = door.offsetMm - door.widthMm / 2 + 1;
    const gapEnd = door.offsetMm + door.widthMm / 2 - 1;
    for (const [a, b] of hatch.segments) {
      const inGap = (p: PtF): boolean => p.x > gapStart && p.x < gapEnd;
      // A segment may touch a jamb but never sit inside the opening.
      expect(inGap(a) && inGap(b)).toBe(false);
    }
  });

  it('takes the lines away when the binding is taken away (negative control)', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const hatch = buildWallHatch({
      house: doc.house,
      storeyId: STOREY,
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
    });
    expect(hatch.wallIds.size).toBe(0);
    expect(hatch.segments).toEqual([]);
    expect(hatch.linePositions(0).length).toBe(0);
  });

  it('packs the buffer at the storey elevation, north to −Z', () => {
    const doc = boundDoc();
    const hatch = buildWallHatch({
      house: doc.house,
      storeyId: STOREY,
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
    });
    const positions = hatch.linePositions(3050);
    expect(positions.length).toBe(hatch.segments.length * 6);
    expect(positions[1]).toBeCloseTo(3.05, 5);
    expect(positions[2]).toBeLessThanOrEqual(0.2);
  });

  /**
   * THE DRIFT GATE on the density.
   *
   * This repo has already shipped a hatch 31× too dense, because a spacing
   * was passed in the wrong unit and nothing compared it to the drawings
   * service. So the canvas constant is not asserted against a literal — it is
   * read out of `services/drawings/projection/style.py` (2.5 PAPER mm) and
   * multiplied by the scale the canvas claims to draw at (1:100), which is
   * exactly `Style.paper_to_model_mm`. Change the Python and this goes red.
   */
  it('hatches at the sheet’s own density, read from the drawings service', () => {
    const source = readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), STYLE_PY_RELATIVE),
      'utf8',
    );
    const match = /^HATCH_SPACING_MM\s*=\s*([0-9.]+)\s*$/m.exec(source);
    expect(match, 'HATCH_SPACING_MM is no longer a module-level float in style.py').not.toBeNull();
    const paperMm = Number(match?.[1]);
    expect(paperMm).toBeGreaterThan(0);
    // `paper_to_model_mm(2.5)` at 1:100 — exact, per that method's own docstring.
    expect(CANVAS_HATCH_SPACING_MM).toBe(paperMm * CANVAS_HATCH_SCALE_DENOMINATOR);
  });

  it('negative control: the gate notices a changed paper spacing', () => {
    const mutated = 'HATCH_SPACING_MM = 4.0\n';
    const match = /^HATCH_SPACING_MM\s*=\s*([0-9.]+)\s*$/m.exec(mutated);
    expect(Number(match?.[1]) * CANVAS_HATCH_SCALE_DENOMINATOR).not.toBe(CANVAS_HATCH_SPACING_MM);
  });
});

describe('clipToConvexQuad', () => {
  const quad: readonly PtF[] = [
    { x: 0, y: 0 },
    { x: 100, y: 0 },
    { x: 100, y: 10 },
    { x: 0, y: 10 },
  ];

  it('keeps the inside part of a crossing line, either winding', () => {
    const c = clipToConvexQuad({ x: -50, y: 5 }, { x: 150, y: 5 }, quad);
    expect(c).toEqual({ a: { x: 0, y: 5 }, b: { x: 100, y: 5 } });
    const reversed = [...quad].reverse();
    expect(clipToConvexQuad({ x: -50, y: 5 }, { x: 150, y: 5 }, reversed)).toEqual(c);
  });

  it('rejects a line that misses', () => {
    expect(clipToConvexQuad({ x: -50, y: 50 }, { x: 150, y: 50 }, quad)).toBeNull();
  });
});

describe('pushDashed', () => {
  it('bakes an on/off cycle into solid pieces, phase advanced from the family start', () => {
    const out: (readonly [PtF, PtF])[] = [];
    // Cycle 10 on, 10 off, starting 5 along the family line: the piece from
    // x=5 to x=45 begins in the middle of an "on" dash.
    pushDashed(
      out,
      { dashes: [10, 10], dashOffset: 0 },
      { x: 0, y: 0 },
      { x: 5, y: 0 },
      { x: 45, y: 0 },
    );
    expect(out.map(([a, b]) => [a.x, b.x])).toEqual([
      [5, 10],
      [20, 30],
      [40, 45],
    ]);
  });

  it('emits a solid family unchanged', () => {
    const out: (readonly [PtF, PtF])[] = [];
    pushDashed(out, { dashes: [], dashOffset: 0 }, { x: 0, y: 0 }, { x: 1, y: 1 }, { x: 9, y: 9 });
    expect(out).toEqual([
      [
        { x: 1, y: 1 },
        { x: 9, y: 9 },
      ],
    ]);
  });
});
