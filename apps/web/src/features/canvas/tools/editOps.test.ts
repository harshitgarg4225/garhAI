/**
 * Spec for THE COMMIT PATH.
 *
 * Everything the tool layer emits is built by a function in `editOps.ts`, and
 * this file is where those payloads are pinned against the real taxonomy in
 * `packages/model/src/ops.ts`. Three things are being defended:
 *
 *  1. **Shape.** Every builder's output survives `validateOpShape` — the same
 *     function the server runs before it will sequence anything.
 *  2. **The end-margin mirror.** `openingOffsetWindow` is a local copy of the
 *     rule inside `validate.ts`, kept for the frame budget. A copy that drifts
 *     means a door previews as legal and folds as rejected, so the boundary
 *     offsets are asserted against the REAL validator, not against a constant.
 *  3. **Delete ordering.** `wall.delete` cascades to its openings, and groups
 *     are atomic — get the order wrong and a mixed delete refuses entirely
 *     rather than half-applying.
 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULTS,
  FIXTURE_IDS,
  fixedId,
  getOpSpec,
  isOp,
  makeTwoRoomPlan,
  makeTwoRoomPlanWithOpenings,
  validateOpAgainstDoc,
  validateOpShape,
  type Op,
  type OpeningSwing,
} from '@garh/model';

import {
  angleDeg,
  balconyAddOp,
  clampOpeningOffset,
  defaultOpeningParams,
  deleteLabel,
  deleteOps,
  dryRun,
  furnitureFootprintMm,
  furniturePlaceOp,
  furnitureTransformOp,
  nextSwing,
  openingAddOp,
  openingFlipOp,
  openingMoveOp,
  openingOffsetWindow,
  openingResizeOp,
  previewWall,
  ringAreaMm2,
  setWallLengthOps,
  stairAddOp,
  SWING_CYCLE,
  toBlock,
  translateWallsOps,
  validateCommit,
  wallAddOp,
  wallMoveOp,
  wallThicknessOp,
} from './editOps';
import { WALL_END_MARGIN_MM } from './constants';
import { nthId, opOfType } from './toolTestKit';

const STOREY = FIXTURE_IDS.groundStorey;

// ---------------------------------------------------------------------------
// Shape
// ---------------------------------------------------------------------------

describe('every builder emits a taxonomy-valid op', () => {
  const built: readonly Op[] = [
    wallAddOp({
      id: nthId('wall', 1),
      storeyId: STOREY,
      a: { x: 0, y: 0 },
      b: { x: 4000, y: 0 },
      thicknessMm: 230,
      kind: 'external',
    }),
    wallMoveOp(FIXTURE_IDS.wallSouth, { x: 0, y: 115 }, { x: 6000, y: 115 }),
    wallThicknessOp(FIXTURE_IDS.wallSouth, 115),
    openingAddOp({
      id: nthId('opening', 1),
      wallId: FIXTURE_IDS.wallSouth,
      kind: 'door',
      widthMm: 900,
      heightMm: 2100,
      sillMm: 0,
      offsetMm: 1495,
      swing: 'in-left',
    }),
    openingMoveOp(FIXTURE_IDS.doorMain, 2000),
    openingMoveOp(FIXTURE_IDS.doorMain, 2000, FIXTURE_IDS.wallNorth),
    openingResizeOp(FIXTURE_IDS.doorMain, { widthMm: 1200 }),
    openingFlipOp(FIXTURE_IDS.doorMain, 'in-right'),
    stairAddOp({
      id: nthId('stair', 1),
      storeyId: STOREY,
      kind: 'dogleg',
      origin: { x: 1150, y: 1150 },
      direction: 'N',
      riserMm: 167,
      treadMm: 290,
      widthMm: 900,
      risersCount: 18,
      landing: { widthMm: 1915, depthMm: 900 },
    }),
    balconyAddOp({
      id: nthId('balcony', 1),
      storeyId: STOREY,
      polygon: [
        { x: 0, y: 0 },
        { x: 2400, y: 0 },
        { x: 2400, y: 900 },
        { x: 0, y: 900 },
      ],
      railingKind: 'ms',
      railingHeightMm: 1000,
      projectionMm: 900,
      slabThicknessMm: 125,
    }),
    furniturePlaceOp({
      id: nthId('furniture', 1),
      storeyId: STOREY,
      catalogId: 'bed-queen-1900x1525',
      pt: { x: 2000, y: 2000 },
      rotationDeg: 90,
    }),
    furnitureTransformOp(FIXTURE_IDS.sofa, { pt: { x: 1000, y: 1000 } }),
  ];

  it('is recognised as an op by the model core', () => {
    for (const op of built) expect(isOp(op)).toBe(true);
  });

  it('names a type that exists in OP_CATALOG', () => {
    for (const op of built) expect(getOpSpec(op.type)).toBeDefined();
  });

  it('passes the real shape validator with no issues at all', () => {
    for (const op of built) {
      expect(validateOpShape(op), `${op.type} should be shape-valid`).toEqual([]);
    }
  });

  it('carries integer millimetres and nothing else — no float ever reaches an op', () => {
    // The hard constraint, checked structurally rather than by eyeballing the
    // payloads: `canonicalJson` in the model core throws on a float, and that
    // backstop should never be the thing that finds one.
    const floats: string[] = [];
    const walk = (value: unknown, path: string): void => {
      if (typeof value === 'number') {
        if (!Number.isInteger(value)) floats.push(`${path} = ${String(value)}`);
        return;
      }
      if (Array.isArray(value)) {
        value.forEach((v, i) => walk(v, `${path}[${String(i)}]`));
        return;
      }
      if (typeof value === 'object' && value !== null) {
        for (const [k, v] of Object.entries(value)) walk(v, `${path}.${k}`);
      }
    };
    built.forEach((op, i) => walk(op.payload, `${op.type}#${String(i)}`));
    expect(floats).toEqual([]);
  });
});

describe('op payloads read exactly as the taxonomy describes them', () => {
  it('wall.add carries the id the caller minted, not one invented by fold', () => {
    const op = opOfType(
      wallAddOp({
        id: nthId('wall', 7),
        storeyId: STOREY,
        a: { x: 0, y: 0 },
        b: { x: 4000, y: 0 },
        thicknessMm: 230,
        kind: 'external',
        loadBearing: true,
      }),
      'wall.add',
    );
    expect(op.payload).toEqual({
      id: nthId('wall', 7),
      storeyId: STOREY,
      a: { x: 0, y: 0 },
      b: { x: 4000, y: 0 },
      thicknessMm: 230,
      kind: 'external',
      loadBearing: true,
    });
  });

  it('omits loadBearing rather than sending undefined', () => {
    const op = opOfType(
      wallAddOp({
        id: nthId('wall', 8),
        storeyId: STOREY,
        a: { x: 0, y: 0 },
        b: { x: 4000, y: 0 },
        thicknessMm: 230,
        kind: 'internal',
      }),
      'wall.add',
    );
    expect('loadBearing' in op.payload).toBe(false);
  });

  it('opening.move omits wallId unless the opening is being re-hosted', () => {
    expect('wallId' in openingMoveOp(FIXTURE_IDS.doorMain, 2000).payload).toBe(false);
    const rehosted = opOfType(
      openingMoveOp(FIXTURE_IDS.doorMain, 2000, FIXTURE_IDS.wallNorth),
      'opening.move',
    );
    expect(rehosted.payload.wallId).toBe(FIXTURE_IDS.wallNorth);
  });

  it('opening.resize sends only the fields that changed', () => {
    const op = opOfType(openingResizeOp(FIXTURE_IDS.doorMain, { widthMm: 1200 }), 'opening.resize');
    expect(op.payload).toEqual({ openingId: FIXTURE_IDS.doorMain, widthMm: 1200 });
  });

  it('balcony.set and furniture.set use the action field, not a new op type', () => {
    const balcony = opOfType(
      balconyAddOp({
        id: nthId('balcony', 1),
        storeyId: STOREY,
        polygon: [
          { x: 0, y: 0 },
          { x: 2400, y: 0 },
          { x: 2400, y: 900 },
        ],
        railingKind: 'glass',
        railingHeightMm: 1000,
        projectionMm: 900,
        slabThicknessMm: 125,
      }),
      'balcony.set',
    );
    expect(balcony.payload.action).toBe('add');

    const furniture = opOfType(
      furniturePlaceOp({
        id: nthId('furniture', 1),
        storeyId: STOREY,
        catalogId: 'sofa-3seat',
        pt: { x: 0, y: 0 },
        rotationDeg: 0,
      }),
      'furniture.set',
    );
    expect(furniture.payload.action).toBe('place');
  });
});

// ---------------------------------------------------------------------------
// The end-margin mirror — the assertion this file exists for
// ---------------------------------------------------------------------------

describe('openingOffsetWindow mirrors the real validator', () => {
  const doc = makeTwoRoomPlan();
  /** wallSouth is (0,0) → (6000,0). */
  const WALL_LENGTH_MM = 6000;

  function outOfWallIssues(offsetMm: number, widthMm: number): string[] {
    const op = openingAddOp({
      id: fixedId('opening', 'PROBE'),
      wallId: FIXTURE_IDS.wallSouth,
      kind: 'door',
      widthMm,
      heightMm: 2100,
      sillMm: 0,
      offsetMm,
      swing: 'in-left',
    });
    return validateOpAgainstDoc(doc, op)
      .filter((i) => i.code === 'OPENING_OUT_OF_WALL')
      .map((i) => i.message);
  }

  it('computes the window from the 115 mm invariant', () => {
    expect(openingOffsetWindow(WALL_LENGTH_MM, 900)).toEqual({ minMm: 565, maxMm: 5435 });
    expect(WALL_END_MARGIN_MM).toBe(115);
  });

  it('agrees with the validator at the exact boundary offsets, for an even width', () => {
    const w = openingOffsetWindow(WALL_LENGTH_MM, 900);
    expect(w).not.toBeNull();
    if (w === null) return;
    expect(outOfWallIssues(w.minMm, 900)).toEqual([]);
    expect(outOfWallIssues(w.maxMm, 900)).toEqual([]);
    expect(outOfWallIssues(w.minMm - 1, 900)).toHaveLength(1);
    expect(outOfWallIssues(w.maxMm + 1, 900)).toHaveLength(1);
  });

  it('agrees at the boundary for an ODD width, where floor/ceil disagree', () => {
    // The asymmetry (`floor` on the near side, `ceil` on the far side) is the
    // single easiest thing to get wrong in a mirrored predicate.
    const w = openingOffsetWindow(WALL_LENGTH_MM, 901);
    expect(w).toEqual({ minMm: 565, maxMm: 5434 });
    if (w === null) return;
    expect(outOfWallIssues(w.minMm, 901)).toEqual([]);
    expect(outOfWallIssues(w.maxMm, 901)).toEqual([]);
    expect(outOfWallIssues(w.maxMm + 1, 901)).toHaveLength(1);
  });

  it('returns null exactly when the validator says the wall is too short', () => {
    // 6000 − 2×115 = 5770 of usable wall. At exactly that width the window
    // collapses to a single legal offset, and both sides must agree it is 3000.
    expect(openingOffsetWindow(WALL_LENGTH_MM, 5770)).toEqual({ minMm: 3000, maxMm: 3000 });
    expect(openingOffsetWindow(WALL_LENGTH_MM, 5771)).toBeNull();
    expect(outOfWallIssues(3000, 5770)).toEqual([]);
    expect(outOfWallIssues(3000, 5771)).toHaveLength(1);
  });

  it('clamps a desired offset into the window instead of refusing', () => {
    expect(clampOpeningOffset(0, WALL_LENGTH_MM, 900)).toBe(565);
    expect(clampOpeningOffset(9999, WALL_LENGTH_MM, 900)).toBe(5435);
    expect(clampOpeningOffset(3000, WALL_LENGTH_MM, 900)).toBe(3000);
    expect(clampOpeningOffset(3000, 1000, 900)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Dimension-first editing — the door the overlays agent uses
// ---------------------------------------------------------------------------

describe('setWallLengthOps', () => {
  const doc = makeTwoRoomPlan();

  it('holds the `a` end and slides `b` along the existing direction', () => {
    const ops = setWallLengthOps(doc, FIXTURE_IDS.wallSouth, 3600, 'a');
    const op = opOfType(ops[0], 'wall.move');
    expect(op.payload).toEqual({
      wallId: FIXTURE_IDS.wallSouth,
      a: { x: 0, y: 0 },
      b: { x: 3600, y: 0 },
    });
    expect(dryRun(doc, ops)).toEqual([]);
  });

  it('holds the `b` end when asked to', () => {
    const op = opOfType(setWallLengthOps(doc, FIXTURE_IDS.wallSouth, 3600, 'b')[0], 'wall.move');
    expect(op.payload.a).toEqual({ x: 2400, y: 0 });
    expect(op.payload.b).toEqual({ x: 6000, y: 0 });
  });

  it('defaults to holding `a`', () => {
    expect(setWallLengthOps(doc, FIXTURE_IDS.wallSouth, 3600)).toEqual(
      setWallLengthOps(doc, FIXTURE_IDS.wallSouth, 3600, 'a'),
    );
  });

  it('emits nothing rather than an op fold would refuse', () => {
    expect(setWallLengthOps(doc, FIXTURE_IDS.wallSouth, 0)).toEqual([]);
    expect(setWallLengthOps(doc, FIXTURE_IDS.wallSouth, -100)).toEqual([]);
    expect(setWallLengthOps(doc, FIXTURE_IDS.wallSouth, 3600.5)).toEqual([]);
    expect(setWallLengthOps(doc, fixedId('wall', 'NOPE'), 3600)).toEqual([]);
  });
});

describe('translateWallsOps', () => {
  const doc = makeTwoRoomPlan();

  it('moves both endpoints by the delta', () => {
    const op = opOfType(translateWallsOps(doc, [FIXTURE_IDS.wallSpine], { x: 115, y: 0 })[0], 'wall.move');
    expect(op.payload.a).toEqual({ x: 3115, y: 0 });
    expect(op.payload.b).toEqual({ x: 3115, y: 4000 });
  });

  it('silently skips ids that are not walls in this document', () => {
    expect(translateWallsOps(doc, [fixedId('wall', 'NOPE')], { x: 115, y: 0 })).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

describe('deleteOps', () => {
  const doc = makeTwoRoomPlanWithOpenings();

  it('puts openings before walls, because wall.delete cascades', () => {
    const ops = deleteOps(doc, [FIXTURE_IDS.wallNorth, FIXTURE_IDS.windowWest]);
    expect(ops.map((o) => o.type)).toEqual(['opening.delete', 'wall.delete']);
    expect(dryRun(doc, ops)).toEqual([]);
  });

  it('drops an opening whose host wall is being deleted anyway', () => {
    // doorMain is hosted on wallSouth. Deleting both must not try to delete the
    // opening twice — the second attempt would be OPENING_UNKNOWN, and an
    // atomic group would then refuse the whole delete.
    const ops = deleteOps(doc, [FIXTURE_IDS.wallSouth, FIXTURE_IDS.doorMain]);
    expect(ops.map((o) => o.type)).toEqual(['wall.delete']);
    expect(dryRun(doc, ops)).toEqual([]);
  });

  it('skips ids that no longer exist', () => {
    expect(deleteOps(doc, [fixedId('wall', 'GONE'), fixedId('opening', 'GONE')])).toEqual([]);
  });

  it('skips rooms — they are derived, and there is no room.delete', () => {
    expect(deleteOps(doc, [fixedId('room', 'R1')])).toEqual([]);
  });

  it('labels the undo toast in plain words', () => {
    expect(deleteLabel(deleteOps(doc, [FIXTURE_IDS.wallNorth]))).toBe('Wall deleted');
    expect(deleteLabel(deleteOps(doc, [FIXTURE_IDS.doorMain]))).toBe('Opening deleted');
    expect(deleteLabel(deleteOps(doc, [FIXTURE_IDS.doorMain, FIXTURE_IDS.windowWest]))).toBe(
      '2 things deleted',
    );
  });
});

// ---------------------------------------------------------------------------
// Dry run
// ---------------------------------------------------------------------------

describe('dryRun / validateCommit', () => {
  const doc = makeTwoRoomPlan();

  it('is silent about a group that would apply cleanly', () => {
    expect(dryRun(doc, [])).toEqual([]);
    expect(validateCommit(doc, [])).toBeNull();
  });

  it('returns the model core’s own sentence for a refusal', () => {
    const duplicate = wallAddOp({
      id: nthId('wall', 9),
      storeyId: STOREY,
      a: { x: 0, y: 0 },
      b: { x: 6000, y: 0 },
      thicknessMm: 230,
      kind: 'external',
    });
    const issues = dryRun(doc, [duplicate]);
    expect(issues.map((i) => i.code)).toContain('WALL_DUPLICATE');

    const block = validateCommit(doc, [duplicate]);
    expect(block?.message).toBe(issues[0]?.message);
    expect(block?.issues).toEqual(issues);
  });

  it('does not touch the document it was handed', () => {
    const before = doc.house.walls.length;
    dryRun(doc, [
      wallAddOp({
        id: nthId('wall', 10),
        storeyId: STOREY,
        a: { x: 0, y: 1150 },
        b: { x: 6000, y: 1150 },
        thicknessMm: 115,
        kind: 'internal',
      }),
    ]);
    expect(doc.house.walls).toHaveLength(before);
  });

  it('toBlock is null for an empty issue list', () => {
    expect(toBlock([])).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Small pure helpers
// ---------------------------------------------------------------------------

describe('geometry helpers', () => {
  it('reports bearings CCW from east, normalised to 0–359', () => {
    expect(angleDeg({ x: 0, y: 0 }, { x: 4000, y: 0 })).toBe(0);
    expect(angleDeg({ x: 0, y: 0 }, { x: 0, y: 4000 })).toBe(90);
    expect(angleDeg({ x: 0, y: 0 }, { x: -4000, y: 0 })).toBe(180);
    expect(angleDeg({ x: 0, y: 0 }, { x: 0, y: -4000 })).toBe(270);
  });

  it('precomputes length and angle on a preview wall', () => {
    const w = previewWall({ x: 0, y: 0 }, { x: 3000, y: 4000 }, 230, 'external');
    expect(w.lengthMm).toBe(5000);
    expect(w.angleDeg).toBe(53);
    expect(w.thicknessMm).toBe(230);
  });

  it('measures a ring, and refuses to guess at fewer than three points', () => {
    expect(
      ringAreaMm2([
        { x: 0, y: 0 },
        { x: 2400, y: 0 },
        { x: 2400, y: 900 },
        { x: 0, y: 900 },
      ]),
    ).toBe(2_160_000);
    expect(ringAreaMm2([{ x: 0, y: 0 }])).toBe(0);
    expect(ringAreaMm2([])).toBe(0);
  });

  it('swaps a furniture footprint on the quarter turns', () => {
    expect(furnitureFootprintMm(1900, 1525, 0)).toEqual({ xMm: 1900, yMm: 1525 });
    expect(furnitureFootprintMm(1900, 1525, 90)).toEqual({ xMm: 1525, yMm: 1900 });
    expect(furnitureFootprintMm(1900, 1525, 180)).toEqual({ xMm: 1900, yMm: 1525 });
    expect(furnitureFootprintMm(1900, 1525, 270)).toEqual({ xMm: 1525, yMm: 1900 });
    expect(furnitureFootprintMm(1900, 1525, -90)).toEqual({ xMm: 1525, yMm: 1900 });
  });
});

describe('opening defaults and swing', () => {
  it('takes its sizes from the model core, not from numbers typed here', () => {
    expect(defaultOpeningParams('door')).toEqual({
      widthMm: DEFAULTS.doorWidthMm,
      heightMm: DEFAULTS.doorHeightMm,
      sillMm: 0,
    });
    expect(defaultOpeningParams('window')).toEqual({
      widthMm: DEFAULTS.windowWidthMm,
      heightMm: DEFAULTS.windowHeightMm,
      sillMm: DEFAULTS.sillDefaultMm,
    });
    expect(defaultOpeningParams('ventilator')).toEqual({
      widthMm: DEFAULTS.ventilatorWidthMm,
      heightMm: DEFAULTS.ventilatorHeightMm,
      sillMm: DEFAULTS.ventilatorSillMm,
    });
  });

  it('cycles the swing back to where it started in four presses', () => {
    let swing: OpeningSwing = 'in-left';
    const seen: OpeningSwing[] = [];
    for (let i = 0; i < 4; i++) {
      swing = nextSwing(swing);
      seen.push(swing);
    }
    expect(seen).toEqual(['in-right', 'out-right', 'out-left', 'in-left']);
    expect(SWING_CYCLE).toHaveLength(4);
  });
});
