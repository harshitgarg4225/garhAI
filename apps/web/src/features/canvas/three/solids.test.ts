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

import { buildGroup } from './geometryBuild';
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
