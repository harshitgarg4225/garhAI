/**
 * Pure-logic spec for the options mini-plan geometry: wall extraction from a
 * real op expansion, storey mapping, bounds (half-thickness inclusive), the
 * mm→viewBox scaling contract with its single Y flip, and the compass-wheel
 * sector math. All integer mm in, integer mm out — no float coordinates.
 */

import { describe, expect, it } from 'vitest';

import {
  boundsOfPolygon,
  boundsOfWalls,
  extractWalls,
  labelForRoomType,
  miniPlanFromEvent,
  miniPlanFromOption,
  onStorey,
  planViewBox,
  roomLabels,
  sectorLabelPoint,
  sectorPath,
  storeyIndexById,
  unionBounds,
} from './planGeometry';
import { miniPlanSchema, planOptionSchema, type OptionOp } from './types';

// ---------------------------------------------------------------------------
// Fixtures — the shape the solver's op expansion actually has
// ---------------------------------------------------------------------------

const OPS: OptionOp[] = [
  { type: 'storey.add', payload: { id: 'storey_g', index: 0, heightMm: 3050 } },
  { type: 'storey.add', payload: { id: 'storey_1', index: 1, heightMm: 3050 } },
  {
    type: 'wall.add',
    payload: {
      id: 'wall_a',
      storeyId: 'storey_g',
      a: { x: 0, y: 0 },
      b: { x: 9000, y: 0 },
      thicknessMm: 230,
      kind: 'external',
    },
  },
  {
    type: 'wall.add',
    payload: {
      id: 'wall_b',
      storeyId: 'storey_g',
      a: { x: 0, y: 0 },
      b: { x: 0, y: 12000 },
      thicknessMm: 230,
      kind: 'external',
    },
  },
  {
    type: 'wall.add',
    payload: {
      id: 'wall_c',
      storeyId: 'storey_1',
      a: { x: 0, y: 3000 },
      b: { x: 9000, y: 3000 },
      thicknessMm: 115,
      kind: 'internal',
    },
  },
  // Malformed: float coordinate — must be skipped, not rounded.
  {
    type: 'wall.add',
    payload: {
      id: 'wall_bad',
      storeyId: 'storey_g',
      a: { x: 0.5, y: 0 },
      b: { x: 100, y: 0 },
      thicknessMm: 115,
      kind: 'internal',
    },
  },
  { type: 'room.assign', payload: { roomId: 'room_1', type: 'living' } },
];

describe('storeyIndexById', () => {
  it('maps storey ids through storey.add index payloads', () => {
    const map = storeyIndexById(OPS);
    expect(map.get('storey_g')).toBe(0);
    expect(map.get('storey_1')).toBe(1);
  });

  it('falls back to first-seen wall order when no storey.add ops exist', () => {
    const ops: OptionOp[] = [
      { type: 'wall.add', payload: { storeyId: 's_x', a: { x: 0, y: 0 }, b: { x: 1, y: 0 } } },
      { type: 'wall.add', payload: { storeyId: 's_y', a: { x: 0, y: 0 }, b: { x: 1, y: 0 } } },
      { type: 'wall.add', payload: { storeyId: 's_x', a: { x: 0, y: 1 }, b: { x: 1, y: 1 } } },
    ];
    const map = storeyIndexById(ops);
    expect(map.get('s_x')).toBe(0);
    expect(map.get('s_y')).toBe(1);
  });
});

describe('extractWalls', () => {
  it('extracts walls with storey indices and defaults', () => {
    const walls = extractWalls(OPS);
    expect(walls).toHaveLength(3); // wall_bad skipped
    expect(walls[0]).toMatchObject({ thicknessMm: 230, kind: 'external', storeyIndex: 0 });
    expect(walls[2]).toMatchObject({ thicknessMm: 115, kind: 'internal', storeyIndex: 1 });
  });

  it('skips non-integer coordinates rather than rounding them', () => {
    const walls = extractWalls(OPS);
    expect(walls.some((w) => w.a.x === 0.5 || w.a.x === 1)).toBe(false);
  });

  it('defaults a missing thickness to the 115mm internal module', () => {
    const walls = extractWalls([
      { type: 'wall.add', payload: { storeyId: 's', a: { x: 0, y: 0 }, b: { x: 500, y: 0 } } },
    ]);
    expect(walls[0]?.thicknessMm).toBe(115);
  });
});

describe('bounds', () => {
  it('includes the half-thickness overhang past centreline endpoints', () => {
    const walls = extractWalls([
      {
        type: 'wall.add',
        payload: {
          storeyId: 's',
          a: { x: 0, y: 0 },
          b: { x: 3000, y: 0 },
          thicknessMm: 230,
          kind: 'external',
        },
      },
    ]);
    expect(boundsOfWalls(walls)).toEqual({ minX: -115, minY: -115, maxX: 3115, maxY: 115 });
  });

  it('returns null for no walls, and unions with polygon bounds', () => {
    expect(boundsOfWalls([])).toBeNull();
    const poly = boundsOfPolygon([
      { x: -500, y: 0 },
      { x: 4000, y: 0 },
      { x: 4000, y: 2000 },
    ]);
    expect(poly).toEqual({ minX: -500, minY: 0, maxX: 4000, maxY: 2000 });
    expect(unionBounds(null, poly)).toEqual(poly);
    expect(unionBounds(poly, null)).toEqual(poly);
    expect(unionBounds(poly, { minX: 0, minY: -100, maxX: 5000, maxY: 100 })).toEqual({
      minX: -500,
      minY: -100,
      maxX: 5000,
      maxY: 2000,
    });
  });
});

describe('planViewBox — the mm→SVG scaling contract', () => {
  const bounds = { minX: 0, minY: 0, maxX: 12000, maxY: 9000 };

  it('pads by per-mille of the larger span, integer math', () => {
    const view = planViewBox(bounds); // pad = 12000 * 60 / 1000 = 720
    expect(view.viewBox).toBe('0 0 13440 10440');
    expect(view.widthMm).toBe(13440);
    expect(view.heightMm).toBe(10440);
  });

  it('flips Y exactly once: plot-up becomes SVG-down', () => {
    const view = planViewBox(bounds);
    expect(view.toView({ x: 0, y: 0 })).toEqual({ x: 720, y: 9720 });
    expect(view.toView({ x: 12000, y: 9000 })).toEqual({ x: 12720, y: 720 });
    // A point HIGHER on the plot renders HIGHER on screen (smaller SVG y).
    const low = view.toView({ x: 0, y: 0 });
    const high = view.toView({ x: 0, y: 9000 });
    expect(high.y).toBeLessThan(low.y);
  });

  it('floors the pad at 60mm for tiny plans', () => {
    const view = planViewBox({ minX: 0, minY: 0, maxX: 300, maxY: 300 });
    expect(view.viewBox).toBe('0 0 420 420'); // 300 + 60*2
  });

  it('keeps thin walls visible: stroke floors at 60mm', () => {
    const view = planViewBox(bounds);
    expect(view.strokeFor(230)).toBe(230);
    expect(view.strokeFor(115)).toBe(115);
    expect(view.strokeFor(10)).toBe(60);
  });

  it('scales the label font with the plan, floored for legibility', () => {
    expect(planViewBox(bounds).labelFontMm).toBe(Math.trunc(13440 / 18));
    expect(planViewBox({ minX: 0, minY: 0, maxX: 300, maxY: 300 }).labelFontMm).toBe(220);
  });

  it('never divides by zero on degenerate bounds', () => {
    const view = planViewBox({ minX: 5, minY: 5, maxX: 5, maxY: 5 });
    expect(view.widthMm).toBeGreaterThan(0);
    expect(view.heightMm).toBeGreaterThan(0);
  });
});

describe('miniPlanFromOption / onStorey', () => {
  const option = planOptionSchema.parse({
    id: 'opt_1',
    rank: 0,
    scores: {},
    ops: OPS,
    signature: [],
    stairAnchorId: 'anchor_1',
    builtUpMm2: 0,
    footprintMm2: 0,
    placements: [
      {
        roomKey: 'living',
        roomType: 'living',
        storeyIndex: 0,
        xMm: 230,
        yMm: 230,
        widthMm: 4000,
        depthMm: 5000,
      },
      {
        roomKey: 'bedroom1',
        roomType: 'bedroom',
        storeyIndex: 1,
        xMm: 230,
        yMm: 230,
        widthMm: 3300,
        depthMm: 3600,
      },
    ],
  });

  it('collects walls, labels and the floors that have geometry', () => {
    const geometry = miniPlanFromOption(option);
    expect(geometry.walls).toHaveLength(3);
    expect(geometry.labels).toHaveLength(2);
    expect(geometry.storeyIndices).toEqual([0, 1]);
    // Label anchors at the placement centre, integer mm.
    expect(geometry.labels[0]).toMatchObject({ label: 'Living', x: 2230, y: 2730 });
  });

  it('filters cleanly to one floor', () => {
    const ground = onStorey(miniPlanFromOption(option), 0);
    expect(ground.walls).toHaveLength(2);
    expect(ground.labels.map((l) => l.label)).toEqual(['Living']);
    const first = onStorey(miniPlanFromOption(option), 1);
    expect(first.walls).toHaveLength(1);
    expect(first.labels.map((l) => l.label)).toEqual(['Bedroom']);
  });

  it('renders silhouettes from the theater miniPlan event payload', () => {
    const payload = miniPlanSchema.parse({
      walls: [{ a: { x: 0, y: 0 }, b: { x: 6000, y: 0 }, thicknessMm: 230 }],
      rooms: [{ label: 'Living', x: 3000, y: 2000 }],
      storeyIndex: 0,
    });
    const geometry = miniPlanFromEvent(payload);
    expect(geometry.walls).toHaveLength(1);
    expect(geometry.labels[0]?.label).toBe('Living');
    expect(geometry.bounds).not.toBeNull();
  });
});

describe('roomLabels / labelForRoomType', () => {
  it('uses the model catalogue label and falls back readably', () => {
    expect(labelForRoomType('living')).toBe('Living');
    expect(labelForRoomType('bedroom_master')).not.toBe('bedroom_master');
    expect(labelForRoomType('weird_type')).toBe('Weird type');
    expect(labelForRoomType('')).toBe('Room');
  });

  it('returns empty for missing placements', () => {
    expect(roomLabels(undefined)).toEqual([]);
  });
});

describe('compass sector math', () => {
  it('anchors N straight up and E straight right', () => {
    expect(sectorLabelPoint(50, 50, 32, 'N')).toEqual({ x: 50, y: 18 });
    expect(sectorLabelPoint(50, 50, 32, 'E')).toEqual({ x: 82, y: 50 });
    expect(sectorLabelPoint(50, 50, 32, 'S')).toEqual({ x: 50, y: 82 });
    expect(sectorLabelPoint(50, 50, 32, 'W')).toEqual({ x: 18, y: 50 });
  });

  it('builds a closed annulus sector path that starts above centre for N', () => {
    const d = sectorPath(50, 50, 18, 46, 'N');
    expect(d.startsWith('M ')).toBe(true);
    expect(d.endsWith('Z')).toBe(true);
    expect(d.match(/A /g)).toHaveLength(2); // outer sweep + inner return
    const m = /^M (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/.exec(d);
    expect(m).not.toBeNull();
    // Start point: inner radius at −22.5° — left of and above the centre.
    expect(Number(m?.[1])).toBeCloseTo(50 + 18 * Math.sin((-22.5 * Math.PI) / 180), 1);
    expect(Number(m?.[2])).toBeLessThan(50);
  });
});
