import { describe, expect, it } from 'vitest';

import { polygonAreaMm2, polygonDoubledAreaMm2, pt, rectPolygon } from './geometry';
import type { WallId } from './ids';
import type { Room, Wall } from './model';
import {
  DEFAULT_JACCARD_THRESHOLD,
  buildHalfEdgeGraph,
  detectRooms,
  matchRooms,
  planarFaces,
  roomCandidates,
} from './rooms';
import { FIXTURE_IDS, fixedId } from './testing';

const STOREY = FIXTURE_IDS.groundStorey;

function wall(tag: string, ax: number, ay: number, bx: number, by: number, thicknessMm = 230): Wall {
  return {
    id: fixedId('wall', tag),
    storeyId: STOREY,
    a: pt(ax, ay),
    b: pt(bx, by),
    thicknessMm,
    kind: thicknessMm >= 200 ? 'external' : 'internal',
    loadBearing: thicknessMm >= 200,
  };
}

/** A 6000 x 4000 box with a 115mm spine at x = 3000. */
function twoRoomWalls(spineX = 3000): Wall[] {
  return [
    wall('WS', 0, 0, 6000, 0),
    wall('WE', 6000, 0, 6000, 4000),
    wall('WN', 6000, 4000, 0, 4000),
    wall('WW', 0, 4000, 0, 0),
    wall('WSP', spineX, 0, spineX, 4000, 115),
  ];
}

describe('half-edge graph', () => {
  it('walks a single closed box into one interior face (CCW) and one exterior face (CW)', () => {
    const graph = buildHalfEdgeGraph([
      wall('A', 0, 0, 1000, 0),
      wall('B', 1000, 0, 1000, 1000),
      wall('C', 1000, 1000, 0, 1000),
      wall('D', 0, 1000, 0, 0),
    ]);
    expect(graph.nodes).toHaveLength(4);
    expect(graph.halfEdges).toHaveLength(8);
    const faces = planarFaces(graph);
    expect(faces).toHaveLength(2);
    const interior = faces.filter((f) => f.doubledAreaMm2 > 0);
    const exterior = faces.filter((f) => f.doubledAreaMm2 < 0);
    expect(interior).toHaveLength(1);
    expect(exterior).toHaveLength(1);
    expect(polygonAreaMm2(interior[0].ring)).toBe(1_000_000);
    expect(polygonDoubledAreaMm2(exterior[0].ring)).toBe(-2_000_000);
  });

  it('splits walls at T-junctions', () => {
    const graph = buildHalfEdgeGraph(twoRoomWalls());
    // 4 box corners + 2 T-junctions where the spine meets south and north
    expect(graph.nodes).toHaveLength(6);
    // 6 boundary edges (south and north each split in two) + 1 spine = 7 edges
    expect(graph.halfEdges).toHaveLength(14);
    expect(graph.nonIntegralCrossings).toBe(0);
  });

  it('splits two walls that cross in the middle', () => {
    const graph = buildHalfEdgeGraph([
      wall('A', 0, 500, 1000, 500, 115),
      wall('B', 500, 0, 500, 1000, 115),
    ]);
    expect(graph.nodes).toHaveLength(5);
    expect(graph.halfEdges).toHaveLength(8);
  });
});

describe('roomCandidates', () => {
  it('finds two rooms with clear (inside-face) polygons', () => {
    const { candidates, outline } = roomCandidates(twoRoomWalls());
    expect(candidates).toHaveLength(2);
    expect(candidates[0].polygon).toEqual(rectPolygon(115, 115, 2943, 3885));
    expect(candidates[1].polygon).toEqual(rectPolygon(3057, 115, 5885, 3885));
    for (const c of candidates) {
      expect(c.areaMm2).toBe(2828 * 3770);
      expect(c.insetFailed).toBe(false);
    }
    // outline is the wall network grown outward by half thickness = the footprint
    expect(outline).toEqual(rectPolygon(-115, -115, 6115, 4115));
  });

  it('is deterministic in candidate order regardless of wall order', () => {
    const a = roomCandidates(twoRoomWalls());
    const shuffled = twoRoomWalls().reverse();
    const b = roomCandidates(shuffled);
    expect(b.candidates.map((c) => c.polygon)).toEqual(a.candidates.map((c) => c.polygon));
  });

  it('finds one room when the spine is removed', () => {
    const { candidates } = roomCandidates(twoRoomWalls().slice(0, 4));
    expect(candidates).toHaveLength(1);
    expect(candidates[0].polygon).toEqual(rectPolygon(115, 115, 5885, 3885));
  });

  it('finds nothing when the walls do not enclose anything', () => {
    const { candidates, outline } = roomCandidates([wall('A', 0, 0, 3000, 0)]);
    expect(candidates).toHaveLength(0);
    expect(outline).toBeNull();
  });

  it('ignores a dangling stub inside a room', () => {
    const walls = [...twoRoomWalls().slice(0, 4), wall('STUB', 1000, 0, 1000, 1500, 115)];
    const { candidates } = roomCandidates(walls);
    expect(candidates).toHaveLength(1);
    // the spur is removed and the split south wall re-merged before insetting,
    // so the room is exactly what it would be without the stub
    expect(candidates[0].polygon).toEqual(rectPolygon(115, 115, 5885, 3885));
  });

  it('handles an L-shaped enclosure', () => {
    const walls = [
      wall('A', 0, 0, 4000, 0),
      wall('B', 4000, 0, 4000, 2000),
      wall('C', 4000, 2000, 2000, 2000),
      wall('D', 2000, 2000, 2000, 4000),
      wall('E', 2000, 4000, 0, 4000),
      wall('F', 0, 4000, 0, 0),
    ];
    const { candidates } = roomCandidates(walls);
    expect(candidates).toHaveLength(1);
    expect(candidates[0].polygon).toHaveLength(6);
    expect(candidates[0].areaMm2).toBeGreaterThan(0);
    expect(candidates[0].insetFailed).toBe(false);
  });
});

describe('matchRooms', () => {
  const roomA: Room = {
    id: fixedId('room', 'A'),
    storeyId: STOREY,
    type: 'living',
    name: 'Living',
    polygon: rectPolygon(115, 115, 2943, 3885),
    areaMm2: 2828 * 3770,
    tags: [],
    locked: false,
    targetAreaMm2: null,
    mustFace: null,
  };
  const roomB: Room = {
    ...roomA,
    id: fixedId('room', 'B'),
    type: 'bedroom',
    name: 'Bedroom 1',
    polygon: rectPolygon(3057, 115, 5885, 3885),
  };

  it('matches identical polygons one-to-one', () => {
    const { candidates } = roomCandidates(twoRoomWalls());
    const matches = matchRooms(candidates, [roomA, roomB]);
    expect(matches.map((m) => m.roomId)).toEqual([roomA.id, roomB.id]);
    expect(matches.every((m) => m.jaccard === 1)).toBe(true);
  });

  it('keeps the best match when a wall moves', () => {
    const { candidates } = roomCandidates(twoRoomWalls(4000));
    const matches = matchRooms(candidates, [roomA, roomB]);
    expect(matches.map((m) => m.roomId)).toEqual([roomA.id, roomB.id]);
    expect(matches[0].jaccard).toBeGreaterThan(DEFAULT_JACCARD_THRESHOLD);
  });

  it('never assigns one existing room to two candidates', () => {
    const { candidates } = roomCandidates(twoRoomWalls());
    const matches = matchRooms(candidates, [roomA]);
    const assigned = matches.filter((m) => m.roomId !== null);
    expect(assigned).toHaveLength(1);
  });

  it('returns no match when nothing overlaps enough', () => {
    const far: Room = { ...roomA, polygon: rectPolygon(50_000, 50_000, 52_000, 52_000) };
    const { candidates } = roomCandidates(twoRoomWalls());
    expect(matchRooms(candidates, [far]).every((m) => m.roomId === null)).toBe(true);
  });
});

describe('detectRooms — id preservation is load-bearing', () => {
  it('assigns deterministic ids to brand-new rooms', () => {
    const first = detectRooms(twoRoomWalls(), STOREY, []);
    const second = detectRooms(twoRoomWalls(), STOREY, []);
    expect(second.rooms.map((r) => r.id)).toEqual(first.rooms.map((r) => r.id));
    expect(first.rooms.every((r) => r.type === 'unassigned' && r.name === '')).toBe(true);
    expect(first.removedRoomIds).toEqual([]);
  });

  it('keeps room ids, types, names and locks when a wall MOVES', () => {
    const detected = detectRooms(twoRoomWalls(), STOREY, []);
    const named: Room[] = [
      { ...detected.rooms[0], type: 'living', name: 'Living', locked: true },
      { ...detected.rooms[1], type: 'bedroom_master', name: 'Master', targetAreaMm2: 12_000_000 },
    ];

    const after = detectRooms(twoRoomWalls(4000), STOREY, named);

    expect(after.rooms).toHaveLength(2);
    expect(after.rooms.map((r) => r.id)).toEqual(named.map((r) => r.id));
    expect(after.rooms[0].type).toBe('living');
    expect(after.rooms[0].name).toBe('Living');
    expect(after.rooms[0].locked).toBe(true);
    expect(after.rooms[1].type).toBe('bedroom_master');
    expect(after.rooms[1].targetAreaMm2).toBe(12_000_000);
    expect(after.removedRoomIds).toEqual([]);

    // geometry DID change: the living room grew by 1000mm
    expect(after.rooms[0].polygon).toEqual(rectPolygon(115, 115, 3943, 3885));
    expect(after.rooms[0].areaMm2).toBe(3828 * 3770);
  });

  it('survives a 100mm nudge without changing a single id', () => {
    const detected = detectRooms(twoRoomWalls(), STOREY, []);
    let rooms = detected.rooms;
    for (const spineX of [3100, 3200, 3300, 3200, 3100, 3000]) {
      const next = detectRooms(twoRoomWalls(spineX), STOREY, rooms);
      expect(next.rooms.map((r) => r.id)).toEqual(rooms.map((r) => r.id));
      rooms = next.rooms;
    }
    expect(rooms.map((r) => r.id)).toEqual(detected.rooms.map((r) => r.id));
  });

  it('reports the id that dies when two rooms genuinely merge', () => {
    const detected = detectRooms(twoRoomWalls(), STOREY, []);
    const named: Room[] = [
      { ...detected.rooms[0], type: 'living', name: 'Living' },
      { ...detected.rooms[1], type: 'bedroom', name: 'Bedroom 1' },
    ];
    // delete the spine: the two rooms become one
    const merged = detectRooms(twoRoomWalls().slice(0, 4), STOREY, named);
    expect(merged.rooms).toHaveLength(1);
    expect(named.map((r) => r.id)).toContain(merged.rooms[0].id);
    expect(merged.removedRoomIds).toHaveLength(1);
    expect(merged.removedRoomIds[0]).not.toBe(merged.rooms[0].id);
  });

  it('ignores walls belonging to another storey', () => {
    const other = twoRoomWalls().map((w) => ({ ...w, storeyId: FIXTURE_IDS.firstStorey }));
    expect(detectRooms(other, STOREY, []).rooms).toHaveLength(0);
  });

  it('does not reuse an id that is already taken elsewhere in the document', () => {
    const natural = detectRooms(twoRoomWalls(), STOREY, []);
    const taken = new Set<string>(natural.rooms.map((r) => r.id));
    const collided = detectRooms(twoRoomWalls(), STOREY, [], taken);
    for (const room of collided.rooms) expect(taken.has(room.id)).toBe(false);
    expect(new Set(collided.rooms.map((r) => r.id)).size).toBe(collided.rooms.length);
  });

  it('reports the bounding walls of each room', () => {
    const { candidates } = roomCandidates(twoRoomWalls());
    const spine: WallId = fixedId('wall', 'WSP');
    expect(candidates[0].wallIds).toContain(spine);
    expect(candidates[1].wallIds).toContain(spine);
  });
});
