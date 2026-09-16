/**
 * solids.test.ts — synthesis against a REAL folded document: the two-room
 * fixture plan, extended with storeys, stairs and balconies through real ops
 * (never hand-built HouseModels — fold's derived slabs and rooms are part of
 * what the synthesis reads).
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  fixedId,
  makeTwoRoomPlanWithOpenings,
  type Op,
  type ProjectDoc,
} from '@garh/model';

import { WORLD_UNITS_PER_MM } from '../core/constants';
import type { PrismCutter } from './booleans';
import {
  OPENING_FALLBACK_PROUD_MM,
  OPENING_PANEL_THICKNESS_MM,
  PARAPET_THICKNESS_MM,
} from './extrusion';
import { buildGroup, type BuiltBucket, type GroupBuild } from './geometryBuild';
import {
  ROOF_GROUP_KEY,
  groupKeysOf,
  roofSolids,
  solidsOfGroup,
  storeyGroupKey,
  storeySolids,
} from './solids';

const GF = fixedId('storey', 'GF');
const STAIR = fixedId('stair', 'ST1');
const BALCONY = fixedId('balcony', 'B1');

function withOps(doc: ProjectDoc, ops: Op[]): ProjectDoc {
  return applyGroup(doc, ops).model;
}

function baseDoc(): ProjectDoc {
  return makeTwoRoomPlanWithOpenings();
}

const STAIR_ADD: Op = {
  type: 'stair.add',
  payload: {
    id: STAIR,
    storeyId: GF,
    kind: 'straight',
    origin: { x: 3300, y: 500 },
    direction: 'N',
    riserMm: 150,
    treadMm: 250,
    widthMm: 900,
    risersCount: 20,
    landing: null,
  },
};

const BALCONY_ADD: Op = {
  type: 'balcony.set',
  payload: {
    action: 'add',
    id: BALCONY,
    storeyId: GF,
    polygon: [
      { x: 1000, y: -900 },
      { x: 2500, y: -900 },
      { x: 2500, y: 0 },
      { x: 1000, y: 0 },
    ],
    railingKind: 'glass',
    railingHeightMm: 1000,
    projectionMm: 900,
    slabThicknessMm: 150,
  },
};

// ---------------------------------------------------------------------------

describe('group enumeration', () => {
  it('one group per storey plus the roof', () => {
    const { house } = baseDoc();
    expect(groupKeysOf(house)).toEqual([storeyGroupKey(GF), ROOF_GROUP_KEY]);
  });

  it('no storeys ⇒ no groups (empty document renders nothing)', () => {
    const empty = {
      ...baseDoc().house,
      storeys: [],
      walls: [],
      slabs: [],
      rooms: [],
      openings: [],
    };
    expect(groupKeysOf(empty)).toEqual([]);
    expect(roofSolids(empty)).toEqual([]);
  });
});

describe('storey synthesis', () => {
  it('every wall becomes a prism registered to its wall id', () => {
    const { house } = baseDoc();
    const solids = storeySolids(house, GF);
    const walls = solids.filter((s) => s.key.startsWith('wall:'));
    expect(walls).toHaveLength(5);
    for (const wall of walls) {
      expect(wall.pick?.kind).toBe('wall');
      expect(wall.pick?.id).toBe(wall.elementId);
      expect(wall.pick?.storeyId).toBe(GF);
    }
  });

  it('openings cut their host wall and add a pickable panel', () => {
    const { house } = baseDoc();
    const solids = storeySolids(house, GF);
    const southWall = solids.find((s) => s.key === `wall:${fixedId('wall', 'WS')}`);
    expect(southWall?.cuts).toHaveLength(1); // the main door
    const westWall = solids.find((s) => s.key === `wall:${fixedId('wall', 'WW')}`);
    expect(westWall?.cuts).toHaveLength(1); // the window

    const panels = solids.filter((s) => s.key.startsWith('opening:'));
    expect(panels).toHaveLength(2);
    for (const panel of panels) expect(panel.pick?.kind).toBe('opening');
    const door = panels.find((p) => p.elementId === fixedId('opening', 'D1'));
    const window = panels.find((p) => p.elementId === fixedId('opening', 'W1'));
    expect(door?.glass).toBe(false);
    expect(window?.glass).toBe(true);
  });

  it('the derived floor slab picks by room point, and the plinth exists under GF', () => {
    const { house } = baseDoc();
    const solids = storeySolids(house, GF);
    const slab = solids.find((s) => s.key.startsWith('slab:'));
    expect(slab).toBeDefined();
    expect(slab?.pickRoomByPoint).toBe(true);
    expect(slab?.surface).toBe('floor');
    // GF FFL 600, slab 150 ⇒ slab spans 450..600 and the plinth 0..450.
    expect(slab?.profile.topMm).toBe(600);
    expect(slab?.profile.baseMm).toBe(450);
    const plinth = solids.find((s) => s.key === 'plinth');
    expect(plinth?.profile.baseMm).toBe(0);
    expect(plinth?.profile.topMm).toBe(450);
    expect(plinth?.pick).toBeNull();
    expect(plinth?.surface).toBe('plinth');
  });

  it('a stair contributes one solid per riser, all picking the stair id', () => {
    const doc = withOps(baseDoc(), [STAIR_ADD]);
    const solids = storeySolids(doc.house, GF);
    const steps = solids.filter((s) => s.key.startsWith(`stair:${STAIR}`));
    expect(steps).toHaveLength(20); // no landing on this one
    for (const step of steps) {
      expect(step.pick).toEqual({ kind: 'stair', id: STAIR, storeyId: GF });
      expect(step.surface).toBe('staircase');
    }
  });

  it('a balcony contributes its slab and glass railing bands, all picking the balcony', () => {
    const doc = withOps(baseDoc(), [BALCONY_ADD]);
    const solids = storeySolids(doc.house, GF);
    const slab = solids.find((s) => s.key === `balcony:${BALCONY}`);
    expect(slab?.pick).toEqual({ kind: 'balcony', id: BALCONY, storeyId: GF });
    expect(slab?.profile.topMm).toBe(600); // hung under the FFL
    expect(slab?.profile.baseMm).toBe(450);
    const rails = solids.filter((s) => s.key.startsWith(`railing:${BALCONY}`));
    // South edge of the balcony sits against the south wall ⇒ 3 railed edges.
    expect(rails).toHaveLength(3);
    for (const rail of rails) {
      expect(rail.glass).toBe(true);
      expect(rail.surface).toBe('railing');
      expect(rail.profile.baseMm).toBe(600);
      expect(rail.profile.topMm).toBe(1600);
    }
  });
});

describe('roof synthesis', () => {
  it('terrace slab + a parapet band per envelope edge', () => {
    const { house } = baseDoc();
    const solids = roofSolids(house);
    const roof = solids.find((s) => s.key === 'roof-slab');
    expect(roof).toBeDefined();
    expect(roof?.surface).toBe('roof');
    expect(roof?.pick).toBeNull();
    // Terrace = GF FFL 600 + height 3000.
    expect(roof?.profile.topMm).toBe(3600);
    expect(roof?.profile.baseMm).toBe(3450);

    const envelope = house.slabs.find((s) => s.storeyId === GF && s.kind === 'floor');
    const parapets = solids.filter((s) => s.key.startsWith('parapet:'));
    expect(parapets.length).toBe(envelope?.polygon.length ?? -1);
    for (const band of parapets) {
      expect(band.profile.baseMm).toBe(3600);
      expect(band.profile.topMm).toBe(3600 + house.levels.parapetMm);
      expect(band.pick).toBeNull();
    }
  });

  it('a top-storey stair cuts the roof and gets a mumty that picks the stair', () => {
    const doc = withOps(baseDoc(), [STAIR_ADD]);
    const solids = roofSolids(doc.house);
    const roof = solids.find((s) => s.key === 'roof-slab');
    expect(roof?.cuts).toHaveLength(1);
    const mumty = solids.find((s) => s.key === `mumty:${STAIR}`);
    expect(mumty?.pick).toEqual({ kind: 'stair', id: STAIR, storeyId: GF });
    expect(mumty?.profile.baseMm).toBe(3600);
  });

  it('an OHT cylinder appears over a shaft room and picks the room', () => {
    const base = baseDoc();
    const roomA = base.house.rooms[0];
    expect(roomA).toBeDefined();
    if (roomA === undefined) return;
    const doc = withOps(base, [
      { type: 'room.assign', payload: { roomId: roomA.id, type: 'shaft' } },
    ]);
    const oht = roofSolids(doc.house).find((s) => s.key === `oht:${roomA.id}`);
    expect(oht).toBeDefined();
    expect(oht?.pick).toEqual({ kind: 'room', id: roomA.id, storeyId: GF });
    expect(oht?.overrideColor).not.toBeNull();
    expect(oht?.profile.polygon.length).toBeGreaterThanOrEqual(12);
  });
});

describe('buildGroup (no engine): honest fallback', () => {
  it('reports holesApplied=false when walls wanted cuts and there is no cutter', () => {
    const { house } = baseDoc();
    const build = buildGroup(house, storeyGroupKey(GF), null, new Set());
    expect(build.holesApplied).toBe(false);
    // Everything still rendered: walls, panels, slab, plinth all have faces.
    expect(build.buckets.length).toBeGreaterThan(0);
    for (const bucket of build.buckets) {
      expect(bucket.positions.length % 9).toBe(0);
      expect(bucket.faceTargets).toHaveLength(bucket.positions.length / 9);
      expect(bucket.normals).toHaveLength(bucket.positions.length);
    }
  });

  it('reports holesApplied=true for a group with nothing to cut', () => {
    const { house } = baseDoc();
    const build = buildGroup(house, ROOF_GROUP_KEY, null, new Set());
    expect(build.holesApplied).toBe(true);
  });

  it('element-scoped material ids split that element into its own bucket', () => {
    const { house } = baseDoc();
    const wallId = fixedId('wall', 'WS');
    const plain = buildGroup(house, storeyGroupKey(GF), null, new Set());
    const scoped = buildGroup(house, storeyGroupKey(GF), null, new Set([wallId]));
    expect(scoped.buckets.length).toBe(plain.buckets.length + 1);
    const own = scoped.buckets.find((b) => b.elementId === wallId);
    expect(own).toBeDefined();
  });

  it('solidsOfGroup routes storey keys and the roof key', () => {
    const { house } = baseDoc();
    expect(solidsOfGroup(house, storeyGroupKey(GF)).storeyId).toBe(GF);
    expect(solidsOfGroup(house, ROOF_GROUP_KEY).storeyId).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The terrace over a set-back lower storey
// ---------------------------------------------------------------------------

describe('terrace over a set-back first floor', () => {
  const FF = fixedId('storey', 'FF');
  const ffWall = (tag: string, ax: number, ay: number, bx: number, by: number): Op => ({
    type: 'wall.add',
    payload: {
      id: fixedId('wall', tag),
      storeyId: FF,
      a: { x: ax, y: ay },
      b: { x: bx, y: by },
      thicknessMm: 230,
      kind: 'external',
    },
  });
  const STOREY_FF: Op = {
    type: 'storey.add',
    payload: { id: FF, index: 1, name: 'First Floor', heightMm: 3000 },
  };

  /** FF shares the south frontage and stops at y = 2500: a rear terrace. */
  function setBackDoc(): ProjectDoc {
    return withOps(baseDoc(), [
      STOREY_FF,
      ffWall('F1', 0, 0, 6000, 0),
      ffWall('F2', 6000, 0, 6000, 2500),
      ffWall('F3', 6000, 2500, 0, 2500),
      ffWall('F4', 0, 2500, 0, 0),
    ]);
  }

  /** FF covers the GF exactly: nothing is exposed. */
  function flushDoc(): ProjectDoc {
    return withOps(baseDoc(), [
      STOREY_FF,
      ffWall('F1', 0, 0, 6000, 0),
      ffWall('F2', 6000, 0, 6000, 4000),
      ffWall('F3', 6000, 4000, 0, 4000),
      ffWall('F4', 0, 4000, 0, 0),
    ]);
  }

  it('the ground storey gets a terrace slab over its exposed rear, at the FF slab level', () => {
    const { house } = setBackDoc();
    const solids = storeySolids(house, GF);
    const terraces = solids.filter((s) => s.key.startsWith('terrace:'));
    expect(terraces).toHaveLength(1);
    const terrace = terraces[0]!;
    expect(terrace.surface).toBe('roof');
    expect(terrace.pick).toBeNull();
    expect(terrace.storeyId).toBe(GF);
    // FF FFL = 600 + 3000 = 3600; its floor slab is 150 ⇒ the terrace IS that
    // slab continued over the exposed strip: 3450..3600.
    expect(terrace.profile.topMm).toBe(3600);
    expect(terrace.profile.baseMm).toBe(3450);
    // Derived slab outlines are the walls' OUTER faces (centreline ± 115 for
    // 230 walls): the exposed strip runs from the FF's north face at 2615
    // to the GF's north face at 4115, across the GF's full outer width.
    const xs = terrace.profile.polygon.map((p) => p.x);
    const ys = terrace.profile.polygon.map((p) => p.y);
    expect(Math.min(...ys)).toBe(2615);
    expect(Math.max(...ys)).toBe(4115);
    expect(Math.min(...xs)).toBe(-115);
    expect(Math.max(...xs)).toBe(6115);
    expect(terrace.cuts).toEqual([]);
    expect(terrace.fallbackProfile).toBeNull();
  });

  it('a parapet stands on the three perimeter edges — none against the FF wall', () => {
    const { house } = setBackDoc();
    const parapets = storeySolids(house, GF).filter((s) => s.key.startsWith('terrace-parapet:'));
    expect(parapets).toHaveLength(3);
    for (const band of parapets) {
      expect(band.surface).toBe('parapet');
      expect(band.pick).toBeNull();
      expect(band.profile.baseMm).toBe(3600);
      expect(band.profile.topMm).toBe(3600 + house.levels.parapetMm);
      // Every band lies in the exposed strip, and none sits ON the FF's
      // north face line (y = 2615) — that wall is the parapet there.
      const ys = band.profile.polygon.map((p) => p.y);
      expect(Math.min(...ys)).toBeGreaterThanOrEqual(2615 - PARAPET_THICKNESS_MM);
      const onFfWall = ys.every((y) => Math.abs(y - 2615) <= PARAPET_THICKNESS_MM);
      expect(onFfWall).toBe(false);
    }
  });

  it('negative control: an FF that covers the GF exactly produces no terrace and no parapet', () => {
    const { house } = flushDoc();
    const solids = storeySolids(house, GF);
    expect(solids.filter((s) => s.key.startsWith('terrace'))).toEqual([]);
  });

  it('the top storey never carries a terrace of its own — that is the roof group', () => {
    const { house } = setBackDoc();
    expect(storeySolids(house, FF).filter((s) => s.key.startsWith('terrace'))).toEqual([]);
    expect(roofSolids(house).find((s) => s.key === 'roof-slab')?.profile.topMm).toBe(6600);
  });

  it('an FF with no walls yet leaves the whole GF outline as terrace', () => {
    const { house } = withOps(baseDoc(), [STOREY_FF]);
    const solids = storeySolids(house, GF);
    const terraces = solids.filter((s) => s.key.startsWith('terrace:'));
    expect(terraces).toHaveLength(1);
    expect(solids.filter((s) => s.key.startsWith('terrace-parapet:'))).toHaveLength(
      terraces[0]!.profile.polygon.length,
    );
  });

  it('builds without an engine (the terrace is plain prisms; no cut wanted)', () => {
    const { house } = setBackDoc();
    const build = buildGroup(house, storeyGroupKey(GF), null, new Set());
    expect(build.holesApplied).toBe(false); // the door + window still want cuts
    expect(build.buckets.some((b) => b.surface === 'roof')).toBe(true);
    expect(build.buckets.some((b) => b.surface === 'parapet')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// The no-WASM fallback must still SHOW the openings
// ---------------------------------------------------------------------------

describe('opening panels in the no-WASM fallback', () => {
  // Fixture: the south wall runs (0,0)→(6000,0), 230 thick, so "across the
  // wall" is model y (world −z). The door D1 sits on it.
  const DOOR_KEY = `opening:${fixedId('opening', 'D1')}`;
  const WALL_THICKNESS_MM = 230;

  const yExtentMm = (profile: { polygon: readonly { y: number }[] }): number => {
    const ys = profile.polygon.map((p) => p.y);
    return Math.max(...ys) - Math.min(...ys);
  };

  it('the panel spec carries a fallback profile proud of BOTH wall faces', () => {
    const { house } = baseDoc();
    const door = storeySolids(house, GF).find((s) => s.key === DOOR_KEY);
    if (door === undefined) throw new Error('no door panel');
    const fallback = door.fallbackProfile;
    if (fallback === null) throw new Error('no fallback profile');
    expect(yExtentMm(door.profile)).toBe(OPENING_PANEL_THICKNESS_MM);
    expect(yExtentMm(fallback)).toBe(WALL_THICKNESS_MM + 2 * OPENING_FALLBACK_PROUD_MM);
    // The real panel is buried inside the wall; the fallback is not.
    expect(yExtentMm(door.profile)).toBeLessThan(WALL_THICKNESS_MM);
    expect(yExtentMm(fallback)).toBeGreaterThan(WALL_THICKNESS_MM);
  });

  /** Across-wall extent of a bucket's vertices, back in mm. */
  const bucketDepthMm = (bucket: BuiltBucket): number => {
    let min = Infinity;
    let max = -Infinity;
    for (let i = 2; i < bucket.positions.length; i += 3) {
      const z = bucket.positions[i] ?? 0;
      if (z < min) min = z;
      if (z > max) max = z;
    }
    return (max - min) / WORLD_UNITS_PER_MM;
  };

  const doorOf = (build: GroupBuild): BuiltBucket => {
    const bucket = build.buckets.find((b) => b.surface === 'door');
    if (bucket === undefined) throw new Error('no door bucket');
    return bucket;
  };

  it('buildGroup draws the proud plate WITHOUT an engine, the 40 mm panel WITH one', () => {
    const { house } = baseDoc();
    const withoutEngine = buildGroup(house, storeyGroupKey(GF), null, new Set());
    // Any cutter at all: the panel swap keys on the engine's presence, not on
    // what the cutter returns for the walls.
    const anyCutter: PrismCutter = { cut: () => ({ positionsMm: new Float32Array(9) }) };
    const withEngine = buildGroup(house, storeyGroupKey(GF), anyCutter, new Set());

    expect(bucketDepthMm(doorOf(withoutEngine))).toBeCloseTo(
      WALL_THICKNESS_MM + 2 * OPENING_FALLBACK_PROUD_MM,
      3,
    );
    expect(bucketDepthMm(doorOf(withEngine))).toBeCloseTo(OPENING_PANEL_THICKNESS_MM, 3);
    expect(withoutEngine.holesApplied).toBe(false);
    expect(withEngine.holesApplied).toBe(true);
  });

  it('solids without a fallback draw the same profile either way (negative control)', () => {
    const { house } = baseDoc();
    const plinth = storeySolids(house, GF).find((s) => s.key === 'plinth');
    expect(plinth?.fallbackProfile).toBeNull();
    const a = buildGroup(house, storeyGroupKey(GF), null, new Set());
    const cutter: PrismCutter = { cut: () => null };
    const b = buildGroup(house, storeyGroupKey(GF), cutter, new Set());
    const plinthOf = (build: GroupBuild): BuiltBucket | undefined =>
      build.buckets.find((x) => x.surface === 'plinth');
    expect(Array.from(plinthOf(a)?.positions ?? [])).toEqual(
      Array.from(plinthOf(b)?.positions ?? []),
    );
  });
});
