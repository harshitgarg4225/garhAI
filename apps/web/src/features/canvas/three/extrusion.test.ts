/**
 * extrusion.test.ts — the profile maths, pinned in integer-mm terms.
 *
 * Every expectation below is hand-computed from the §3 model shapes: FFLs
 * from plinth + storey heights, wall tops under the slab they carry, opening
 * boxes placed along the host wall's real geometry.
 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULTS,
  applyGroup,
  fixedId,
  makeTwoRoomPlanWithOpenings,
  type Opening,
  type Stair,
  type Wall,
} from '@garh/model';

import {
  OPENING_CUT_SLACK_MM,
  balconyRailingFootprintsF,
  edgeTouchesWall,
  ensureCcwF,
  fflOfIndexMm,
  openingCutProfileF,
  openingPanelProfileF,
  parapetSegmentFootprintsF,
  stairSolidProfilesF,
  storeySpanMm,
  terraceLevelMm,
  wallFootprintF,
} from './extrusion';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const GF = fixedId('storey', 'GF');
const FF = fixedId('storey', 'FF');

/** The two-room GF plan plus a first floor, so cross-storey spans are real. */
function twoStoreyDoc() {
  const doc = makeTwoRoomPlanWithOpenings();
  return applyGroup(doc, [
    { type: 'storey.add', payload: { id: FF, index: 1, name: 'First Floor', heightMm: 3000 } },
  ]).model;
}

function wallOf(house: { walls: readonly Wall[] }, id: string): Wall {
  const wall = house.walls.find((w) => w.id === id);
  if (wall === undefined) throw new Error(`fixture wall missing: ${id}`);
  return wall;
}

function openingOf(house: { openings: readonly Opening[] }, id: string): Opening {
  const opening = house.openings.find((o) => o.id === id);
  if (opening === undefined) throw new Error(`fixture opening missing: ${id}`);
  return opening;
}

// ---------------------------------------------------------------------------
// Elevations
// ---------------------------------------------------------------------------

describe('storey spans', () => {
  it('derives GF from plinth and stops its walls under the FF slab', () => {
    const { house } = twoStoreyDoc();
    const span = storeySpanMm(house, GF);
    expect(span).not.toBeNull();
    // fold derives FFLs: GF FFL = plinth 600, FF FFL = 600 + 3000 = 3600.
    expect(span?.baseMm).toBe(600);
    expect(span?.ceilingMm).toBe(3600);
    // FF's slabThicknessMm is the default 150 ⇒ GF walls stop at 3450.
    expect(span?.wallTopMm).toBe(3600 - 150);
    expect(span?.slabAboveThicknessMm).toBe(150);
  });

  it('gives the top storey the full height minus its own slab thickness', () => {
    const { house } = twoStoreyDoc();
    const span = storeySpanMm(house, FF);
    expect(span?.baseMm).toBe(3600);
    expect(span?.ceilingMm).toBe(6600);
    expect(span?.wallTopMm).toBe(6600 - 150);
  });

  it('terrace level is top FFL + height', () => {
    const { house } = twoStoreyDoc();
    expect(terraceLevelMm(house)).toBe(6600);
  });

  it('falls back to plinth + running heights when fflPerStoreyMm is short', () => {
    const { house } = twoStoreyDoc();
    const truncated = { ...house, levels: { ...house.levels, fflPerStoreyMm: [] } };
    expect(fflOfIndexMm(truncated, 0)).toBe(600);
    expect(fflOfIndexMm(truncated, 1)).toBe(3600);
  });

  it('returns null for an unknown storey', () => {
    const { house } = twoStoreyDoc();
    expect(storeySpanMm(house, 'storey_nope')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Wall footprints
// ---------------------------------------------------------------------------

describe('wall footprints', () => {
  it('widens the centreline by half the thickness, CCW', () => {
    const { house } = twoStoreyDoc();
    // South wall: (0,0)→(6000,0), 230 thick. Left normal is +Y.
    const wall = wallOf(house, fixedId('wall', 'WS'));
    const quad = wallFootprintF(wall);
    expect(quad).not.toBeNull();
    expect(quad).toEqual([
      { x: 0, y: -115 },
      { x: 6000, y: -115 },
      { x: 6000, y: 115 },
      { x: 0, y: 115 },
    ]);
    // CCW: the ring survives ensureCcwF unchanged.
    expect(ensureCcwF(quad ?? [])).toEqual(quad);
  });

  it('rejects a degenerate wall instead of emitting NaN', () => {
    const wall: Wall = {
      id: fixedId('wall', 'ZZ'),
      storeyId: GF,
      a: { x: 100, y: 100 },
      b: { x: 100, y: 100 },
      thicknessMm: 115,
      kind: 'internal',
      loadBearing: false,
    };
    expect(wallFootprintF(wall)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Opening boxes against the host wall's geometry
// ---------------------------------------------------------------------------

describe('opening cut boxes', () => {
  it('places the door cut at its centre offset, full height from the FFL', () => {
    const { house } = twoStoreyDoc();
    const wall = wallOf(house, fixedId('wall', 'WS'));
    const door = openingOf(house, fixedId('opening', 'D1'));
    // door: width 900, offset 1500, sill 0, height 2100; GF base 600.
    const cut = openingCutProfileF(wall, door, 600, 3450);
    expect(cut).not.toBeNull();
    expect(cut?.baseMm).toBe(600);
    expect(cut?.topMm).toBe(600 + 2100);
    const xs = (cut?.polygon ?? []).map((p) => p.x);
    const ys = (cut?.polygon ?? []).map((p) => p.y);
    expect(Math.min(...xs)).toBe(1500 - 450);
    expect(Math.max(...xs)).toBe(1500 + 450);
    // Cut depth: half thickness + slack, both sides.
    expect(Math.min(...ys)).toBe(-(115 + OPENING_CUT_SLACK_MM));
    expect(Math.max(...ys)).toBe(115 + OPENING_CUT_SLACK_MM);
  });

  it('starts the window cut at its sill', () => {
    const { house } = twoStoreyDoc();
    const wall = wallOf(house, fixedId('wall', 'WW'));
    const window = openingOf(house, fixedId('opening', 'W1'));
    const cut = openingCutProfileF(wall, window, 600, 3450);
    expect(cut?.baseMm).toBe(600 + DEFAULTS.sillDefaultMm);
    expect(cut?.topMm).toBe(600 + DEFAULTS.sillDefaultMm + DEFAULTS.windowHeightMm);
  });

  it('clamps the along-wall span to the wall and drops fully-off openings', () => {
    const { house } = twoStoreyDoc();
    const wall = wallOf(house, fixedId('wall', 'WS')); // 6000 long
    const door = openingOf(house, fixedId('opening', 'D1'));
    const nearEnd: Opening = { ...door, offsetMm: 5900 }; // spills past 6000
    const cut = openingCutProfileF(wall, nearEnd, 600, 3450);
    const xs = (cut?.polygon ?? []).map((p) => p.x);
    expect(Math.max(...xs)).toBe(6000);

    const offWall: Opening = { ...door, offsetMm: 7000 };
    expect(openingCutProfileF(wall, offWall, 600, 3450)).toBeNull();
  });

  it('clamps the top under the wall top and drops a sill above it', () => {
    const { house } = twoStoreyDoc();
    const wall = wallOf(house, fixedId('wall', 'WS'));
    const door = openingOf(house, fixedId('opening', 'D1'));
    const tall: Opening = { ...door, heightMm: 4000 };
    expect(openingCutProfileF(wall, tall, 600, 3450)?.topMm).toBe(3450);

    const skyLight: Opening = { ...door, sillMm: 3000 }; // 600+3000 > 3450
    expect(openingCutProfileF(wall, skyLight, 600, 3450)).toBeNull();
  });

  it('panel shares the cut placement but is a thin slice of the wall depth', () => {
    const { house } = twoStoreyDoc();
    const wall = wallOf(house, fixedId('wall', 'WS'));
    const door = openingOf(house, fixedId('opening', 'D1'));
    const panel = openingPanelProfileF(wall, door, 600, 3450);
    const ys = (panel?.polygon ?? []).map((p) => p.y);
    expect(Math.min(...ys)).toBe(-20);
    expect(Math.max(...ys)).toBe(20);
    expect(panel?.baseMm).toBe(600);
    expect(panel?.topMm).toBe(2700);
  });
});

// ---------------------------------------------------------------------------
// Stairs — straight flights + landing boxes, from the params
// ---------------------------------------------------------------------------

const STAIR_BASE: Stair = {
  id: fixedId('stair', 'ST1'),
  storeyId: GF,
  kind: 'straight',
  origin: { x: 0, y: 0 },
  direction: 'N',
  riserMm: 150,
  treadMm: 250,
  widthMm: 900,
  risersCount: 20,
  landing: null,
};

describe('stair solids', () => {
  it('emits one stepped box per riser, climbing from the storey base', () => {
    const profiles = stairSolidProfilesF(STAIR_BASE, 600);
    expect(profiles).toHaveLength(20);
    const first = profiles[0];
    const last = profiles[19];
    expect(first?.baseMm).toBe(600);
    expect(first?.topMm).toBe(750);
    expect(last?.topMm).toBe(600 + 20 * 150); // arrives at the next FFL
    // Direction N with right-hand perp ⇒ step 1 spans y ∈ [0,250], x ∈ [0,900].
    const xs = (first?.polygon ?? []).map((p) => p.x);
    const ys = (first?.polygon ?? []).map((p) => p.y);
    expect(Math.min(...xs)).toBe(0);
    expect(Math.max(...xs)).toBe(900);
    expect(Math.min(...ys)).toBe(0);
    expect(Math.max(...ys)).toBe(250);
  });

  it('adds the landing box at the top of the run when the model carries one', () => {
    const withLanding: Stair = { ...STAIR_BASE, landing: { widthMm: 900, depthMm: 1000 } };
    const profiles = stairSolidProfilesF(withLanding, 600);
    expect(profiles).toHaveLength(21);
    const landing = profiles[20];
    expect(landing?.topMm).toBe(600 + 3000);
    expect(landing?.baseMm).toBe(600 + 3000 - 150);
    const ys = (landing?.polygon ?? []).map((p) => p.y);
    expect(Math.min(...ys)).toBe(20 * 250); // starts where the run ends
    expect(Math.max(...ys)).toBe(20 * 250 + 1000);
  });

  it('renders dogleg/L/U as the SAME straight run — the documented limitation', () => {
    // The model stores one origin + direction + landing (inherited fact 3);
    // inventing the turn would draw geometry the model does not carry. This
    // spec pins the honesty: kind changes nothing about the solid.
    const straight = stairSolidProfilesF(
      { ...STAIR_BASE, landing: { widthMm: 900, depthMm: 1000 } },
      600,
    );
    const dogleg = stairSolidProfilesF(
      { ...STAIR_BASE, kind: 'dogleg', landing: { widthMm: 900, depthMm: 1000 } },
      600,
    );
    expect(dogleg).toEqual(straight);
  });
});

// ---------------------------------------------------------------------------
// Parapet and railings
// ---------------------------------------------------------------------------

describe('parapet bands', () => {
  it('insets one band per perimeter edge, inside the ring', () => {
    const square = [
      { x: 0, y: 0 },
      { x: 6000, y: 0 },
      { x: 6000, y: 4000 },
      { x: 0, y: 4000 },
    ];
    const bands = parapetSegmentFootprintsF(square, 115);
    expect(bands).toHaveLength(4);
    // First band hugs the south edge: y within [0, 115].
    const south = bands[0] ?? [];
    for (const p of south) {
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(115);
    }
    // Corner extension: half a thickness past each end.
    const xs = south.map((p) => p.x);
    expect(Math.min(...xs)).toBe(-57.5);
    expect(Math.max(...xs)).toBe(6057.5);
  });
});

describe('balcony railings', () => {
  const walls: Wall[] = [
    {
      id: fixedId('wall', 'WS'),
      storeyId: GF,
      a: { x: 0, y: 0 },
      b: { x: 6000, y: 0 },
      thicknessMm: 230,
      kind: 'external',
      loadBearing: true,
    },
  ];

  it('detects the wall-adjacent edge', () => {
    expect(edgeTouchesWall({ x: 1000, y: 0 }, { x: 2500, y: 0 }, walls)).toBe(true);
    expect(edgeTouchesWall({ x: 1000, y: -900 }, { x: 2500, y: -900 }, walls)).toBe(false);
  });

  it('rails every edge except the one against the building', () => {
    // A balcony hanging south off the south wall.
    const balcony = [
      { x: 1000, y: -900 },
      { x: 2500, y: -900 },
      { x: 2500, y: 0 },
      { x: 1000, y: 0 },
    ];
    const bands = balconyRailingFootprintsF(balcony, walls);
    expect(bands).toHaveLength(3);
  });
});
