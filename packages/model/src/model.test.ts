import { describe, expect, it } from 'vitest';

import { polygonAreaMm2 } from './geometry';
import {
  DEFAULTS,
  HABITABLE_ROOM_TYPES,
  ROOM_TYPES,
  SCHEMA_VERSION,
  WET_ROOM_TYPES,
  buildingHeightMm,
  builtUpAreaMm2,
  effectiveLintelMm,
  effectiveSillMm,
  emptyHouseModel,
  emptyProjectDoc,
  findRoom,
  findStorey,
  findWall,
  isHabitableRoomType,
  isWetRoomType,
  openingsOfWall,
  roomDisplayName,
  roomsOfStorey,
  storeyIndex,
  wallsOfStorey,
} from './model';
import { FIXTURE_IDS, fixedId, makeEmptyDoc, makeTwoRoomPlanWithOpenings } from './testing';

describe('empty documents', () => {
  it('are at the current schema version and hold nothing', () => {
    const doc = emptyProjectDoc();
    expect(doc.schemaVersion).toBe(SCHEMA_VERSION);
    expect(doc.house.schemaVersion).toBe(SCHEMA_VERSION);
    expect(doc.house.storeys).toEqual([]);
    expect(doc.house.walls).toEqual([]);
    expect(doc.house.facade.kitId).toBeNull();
    expect(doc.plot.boundary).toEqual([]);
    expect(doc.plot.northDeg).toBe(0);
    expect(doc.brief.vastuMode).toBe('off');
    expect(doc.annotations).toEqual([]);
  });

  it('default to Indian display units', () => {
    expect(makeEmptyDoc().house.meta.unitsDisplay).toBe('ft-in');
    expect(emptyHouseModel('m').meta.unitsDisplay).toBe('m');
  });

  it('carry Indian residential defaults, all integer mm', () => {
    for (const [key, value] of Object.entries(DEFAULTS)) {
      if (typeof value === 'number') {
        expect(Number.isSafeInteger(value), `${key} must be integer mm`).toBe(true);
      }
    }
    expect(DEFAULTS.externalWallThicknessMm).toBe(230);
    expect(DEFAULTS.internalWallThicknessMm).toBe(115);
    expect(DEFAULTS.sillDefaultMm).toBe(900);
  });
});

describe('room type taxonomy', () => {
  it('has no duplicates', () => {
    expect(new Set(ROOM_TYPES).size).toBe(ROOM_TYPES.length);
  });

  it('classifies habitable and wet rooms', () => {
    expect(isHabitableRoomType('bedroom')).toBe(true);
    expect(isHabitableRoomType('bath')).toBe(false);
    expect(isWetRoomType('kitchen')).toBe(true);
    expect(isWetRoomType('living')).toBe(false);
    for (const t of [...HABITABLE_ROOM_TYPES, ...WET_ROOM_TYPES]) {
      expect(ROOM_TYPES).toContain(t);
    }
    // a room is never both — the NBC minimums would contradict
    expect(HABITABLE_ROOM_TYPES.filter((t) => WET_ROOM_TYPES.includes(t))).toEqual([]);
  });

  it('falls back to the type label when a room has no name', () => {
    const room = {
      id: fixedId('room', 'RA'),
      storeyId: FIXTURE_IDS.groundStorey,
      type: 'bedroom_master' as const,
      name: '',
      polygon: [],
      areaMm2: 0,
      tags: [],
      locked: false,
      targetAreaMm2: null,
      mustFace: null,
    };
    expect(roomDisplayName(room)).toBe('Master Bedroom');
    expect(roomDisplayName({ ...room, name: 'Mum & Dad' })).toBe('Mum & Dad');
    expect(roomDisplayName({ ...room, type: 'unassigned' }, 2)).toBe('Room 2');
  });
});

describe('lookups on a folded document', () => {
  const doc = makeTwoRoomPlanWithOpenings();

  it('finds elements by id', () => {
    expect(findStorey(doc.house, FIXTURE_IDS.groundStorey)?.name).toBe('Ground Floor');
    expect(storeyIndex(doc.house, FIXTURE_IDS.groundStorey)).toBe(0);
    expect(findWall(doc.house, FIXTURE_IDS.wallSouth)?.thicknessMm).toBe(230);
    expect(findRoom(doc.house, doc.house.rooms[0]!.id)?.storeyId).toBe(FIXTURE_IDS.groundStorey);
    expect(findWall(doc.house, fixedId('wall', 'ZZ'))).toBeUndefined();
  });

  it('groups elements by parent', () => {
    expect(wallsOfStorey(doc.house, FIXTURE_IDS.groundStorey)).toHaveLength(5);
    expect(roomsOfStorey(doc.house, FIXTURE_IDS.groundStorey)).toHaveLength(2);
    expect(openingsOfWall(doc.house, FIXTURE_IDS.wallSouth)).toHaveLength(1);
    expect(openingsOfWall(doc.house, FIXTURE_IDS.wallEast)).toHaveLength(0);
  });

  it('resolves sill and lintel defaults through the storey', () => {
    expect(effectiveSillMm(doc.house, FIXTURE_IDS.groundStorey)).toBe(DEFAULTS.sillDefaultMm);
    expect(effectiveLintelMm(doc.house, FIXTURE_IDS.groundStorey)).toBe(DEFAULTS.lintelDefaultMm);
  });

  it('computes building height as plinth plus storey heights', () => {
    expect(buildingHeightMm(doc.house)).toBe(DEFAULTS.plinthMm + DEFAULTS.storeyHeightMm);
  });

  it('computes built-up area from the derived slabs', () => {
    expect(builtUpAreaMm2(doc.house, polygonAreaMm2)).toBe(6230 * 4230);
  });
});
