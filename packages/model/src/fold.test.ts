import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  CANONICAL_JSON_SPEC,
  CanonicalJsonError,
  STATE_HASH_ALGORITHM,
  UndoStack,
  applyGroup,
  applyMergePatch,
  canonicalJson,
  compareCodePoints,
  docHash,
  fold,
  invertMergePatch,
  lockedRoomIds,
  replay,
  stairFootprintPolygon,
  stateHash,
  storeyBuiltUpAreaMm2,
  storeyCarpetAreaMm2,
  tryFold,
  wallLengthMm,
} from './fold';
import { polygonAreaMm2, polygonKey, rectPolygon } from './geometry';
import { emptyProjectDoc } from './model';
import type { GroupId } from './ids';
import { DEFAULTS, ROOM_TYPES } from './model';
import type { ProjectDoc, RoomType } from './model';
import type { Op } from './ops';
import {
  FIXTURE_IDS,
  fixedId,
  makeEmptyDoc,
  makeTwoRoomPlan,
  makeTwoRoomPlanWithOpenings,
  twoRoomPlanOps,
} from './testing';

// ---------------------------------------------------------------------------
// canonicalJson
// ---------------------------------------------------------------------------

describe('canonicalJson (cross-language contract)', () => {
  it('is versioned so a rule change is visible', () => {
    expect(CANONICAL_JSON_SPEC).toBe('garh-canonical-json/v1');
    expect(STATE_HASH_ALGORITHM).toBe('sha256(garh-canonical-json/v1)');
  });

  it('sorts object keys and uses no whitespace', () => {
    expect(canonicalJson({ b: 1, a: 2, C: 3 })).toBe('{"C":3,"a":2,"b":1}');
    expect(canonicalJson({ z: { y: 1, x: 2 } })).toBe('{"z":{"x":2,"y":1}}');
  });

  it('omits keys whose value is undefined, and keeps nulls', () => {
    expect(canonicalJson({ a: undefined, b: null, c: 1 })).toBe('{"b":null,"c":1}');
  });

  it('keeps array order and rejects undefined elements', () => {
    expect(canonicalJson([3, 1, 2])).toBe('[3,1,2]');
    expect(() => canonicalJson([1, undefined, 2])).toThrow(CanonicalJsonError);
  });

  it('refuses floats — the reason the hash is portable', () => {
    expect(() => canonicalJson({ mm: 1.5 })).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(Number.NaN)).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(Number.POSITIVE_INFINITY)).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(1e21)).toThrow(CanonicalJsonError);
  });

  it('normalises -0 to 0', () => {
    expect(canonicalJson(-0)).toBe('0');
    expect(canonicalJson({ x: -0 })).toBe('{"x":0}');
  });

  it('escapes exactly the characters JSON requires, with lowercase \\u00xx', () => {
    expect(canonicalJson('a"b\\c')).toBe('"a\\"b\\\\c"');
    expect(canonicalJson('\n\t\r\b\f')).toBe('"\\n\\t\\r\\b\\f"');
    expect(canonicalJson('\u0000\u001f')).toBe('"\\u0000\\u001f"');
    // non-ASCII stays literal (ensure_ascii=False in the Python mirror)
    expect(canonicalJson('₹ ½ मीटर')).toBe('"₹ ½ मीटर"');
    // forward slash and U+2028 are NOT escaped
    expect(canonicalJson('a/b\u2028')).toBe('"a/b\u2028"');
  });

  it('rejects lone surrogates instead of hashing mojibake', () => {
    expect(() => canonicalJson('\ud800')).toThrow(CanonicalJsonError);
    expect(() => canonicalJson('\udc00x')).toThrow(CanonicalJsonError);
    expect(canonicalJson('👍')).toBe('"👍"');
  });

  it('rejects values JSON cannot express', () => {
    expect(() => canonicalJson(new Date())).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(new Map())).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(() => 1)).toThrow(CanonicalJsonError);
    expect(() => canonicalJson(1n)).toThrow(CanonicalJsonError);
  });

  it('names the path of the offending value', () => {
    try {
      canonicalJson({ house: { walls: [{ thicknessMm: 114.5 }] } });
      expect.unreachable();
    } catch (e) {
      expect((e as CanonicalJsonError).path).toBe('.house.walls[0].thicknessMm');
    }
  });

  it('compares keys by code point, not UTF-16 code unit', () => {
    expect(compareCodePoints('a', 'b')).toBe(-1);
    expect(compareCodePoints('b', 'a')).toBe(1);
    expect(compareCodePoints('a', 'a')).toBe(0);
    expect(compareCodePoints('a', 'ab')).toBe(-1);
    // U+FFFD sorts BEFORE any astral character by code point, even though its
    // UTF-16 code unit is larger than a surrogate lead.
    expect(compareCodePoints('\ufffd', '\u{1f44d}')).toBe(-1);
  });
});

describe('stateHash', () => {
  it('is a 64-char lowercase hex sha256 of the canonical JSON', () => {
    const h = stateHash({ a: 1 });
    expect(h).toMatch(/^[0-9a-f]{64}$/);
    // sha256 of the exact bytes `{"a":1}`
    expect(h).toBe('015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862');
  });

  it('ignores key order but not values', () => {
    expect(stateHash({ a: 1, b: 2 })).toBe(stateHash({ b: 2, a: 1 }));
    expect(stateHash({ a: 1, b: 2 })).not.toBe(stateHash({ a: 2, b: 1 }));
  });

  it('treats an omitted optional field and an explicit undefined as the same', () => {
    expect(stateHash({ a: 1 })).toBe(stateHash({ a: 1, b: undefined }));
    expect(stateHash({ a: 1 })).not.toBe(stateHash({ a: 1, b: null }));
  });
});

// ---------------------------------------------------------------------------
// merge patch
// ---------------------------------------------------------------------------

describe('RFC 7386 merge patch', () => {
  it('merges, replaces and deletes', () => {
    expect(applyMergePatch({ a: 1, b: { c: 2, d: 3 } }, { b: { c: 9 } })).toEqual({
      a: 1,
      b: { c: 9, d: 3 },
    });
    expect(applyMergePatch({ a: 1, b: 2 }, { b: null })).toEqual({ a: 1 });
    expect(applyMergePatch({ a: [1, 2] }, { a: [3] })).toEqual({ a: [3] });
  });

  it('inverts exactly', () => {
    const cases: [Record<string, unknown>, Record<string, unknown>][] = [
      [{ a: 1 }, { a: 2 }],
      [{ a: 1 }, { b: 2 }],
      [{ a: 1, b: 2 }, { b: null }],
      [{ a: { b: 1, c: 2 } }, { a: { b: 9 } }],
      [{ a: { b: 1 } }, { a: null }],
      [{}, { deep: { nested: { value: 7 } } }],
    ];
    for (const [target, patch] of cases) {
      const t = target as Parameters<typeof applyMergePatch>[0];
      const p = patch as Parameters<typeof applyMergePatch>[1];
      const forward = applyMergePatch(t, p);
      const back = applyMergePatch(forward, invertMergePatch(t, p));
      expect(back).toEqual(target);
    }
  });
});

// ---------------------------------------------------------------------------
// determinism
// ---------------------------------------------------------------------------

describe('fold determinism', () => {
  it('folding the same ops twice gives the same state hash', () => {
    const a = replay(twoRoomPlanOps());
    const b = replay(twoRoomPlanOps());
    expect(docHash(a)).toBe(docHash(b));
  });

  it('replay and applyGroup agree', () => {
    const grouped = applyGroup(makeEmptyDoc(), twoRoomPlanOps()).model;
    expect(docHash(replay(twoRoomPlanOps(), makeEmptyDoc()))).toBe(docHash(grouped));
  });

  it('never mutates its input', () => {
    const doc = makeTwoRoomPlan();
    const before = docHash(doc);
    fold(doc, { type: 'plot.set_north', payload: { deg: 90 } });
    expect(docHash(doc)).toBe(before);
  });

  it('is order-insensitive for independent ops (arrays are canonically sorted)', () => {
    const ops = twoRoomPlanOps();
    const walls = ops.filter((o) => o.type === 'wall.add');
    const rest = ops.filter((o) => o.type !== 'wall.add');
    const forward = replay([...rest, ...walls]);
    const backward = replay([...rest, ...walls.slice().reverse()]);
    // Room IDENTITY is deliberately path-dependent: matchRooms keeps a
    // surviving room's id across edits (the undo-integrity contract in
    // rooms.ts), so a different wall order can hand the same final face a
    // different birth id. Everything else must be byte-identical, so compare
    // with each room id neutralised to its polygon's canonical key.
    const neutralised = (doc: ProjectDoc): string => {
      let json = canonicalJson(doc);
      for (const room of doc.house.rooms) {
        json = json.replaceAll(room.id, `room@${polygonKey(room.polygon)}`);
      }
      return json;
    };
    expect(neutralised(backward)).toBe(neutralised(forward));
  });

  it('derives rooms, slabs and levels rather than trusting the op stream', () => {
    const doc = makeTwoRoomPlan();
    expect(doc.house.rooms).toHaveLength(2);
    expect(doc.house.rooms.every((r) => r.storeyId === FIXTURE_IDS.groundStorey)).toBe(true);
    expect(doc.house.rooms.map((r) => r.areaMm2)).toEqual([2828 * 3770, 2828 * 3770]);
    expect(doc.house.slabs).toHaveLength(1);
    expect(doc.house.slabs[0]!.polygon).toEqual(rectPolygon(-115, -115, 6115, 4115));
    expect(doc.house.levels.fflPerStoreyMm).toEqual([DEFAULTS.plinthMm]);
    expect(doc.house.storeys[0]?.level.fflMm).toBe(DEFAULTS.plinthMm);
  });

  it('keeps element arrays sorted by id', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const ids = doc.house.walls.map((w) => w.id);
    expect(ids).toEqual([...ids].sort());
    const openingIds = doc.house.openings.map((o) => o.id);
    expect(openingIds).toEqual([...openingIds].sort());
  });

  it('re-derives FFLs when a storey height changes', () => {
    const two = fold(makeTwoRoomPlan(), {
      type: 'storey.add',
      payload: { id: FIXTURE_IDS.firstStorey, index: 1, heightMm: 3000 },
    }).model;
    expect(two.house.levels.fflPerStoreyMm).toEqual([600, 3600]);
    const taller = fold(two, {
      type: 'storey.set_height',
      payload: { storeyId: FIXTURE_IDS.groundStorey, heightMm: 3300 },
    }).model;
    expect(taller.house.levels.fflPerStoreyMm).toEqual([600, 3900]);
  });
});

// ---------------------------------------------------------------------------
// inverse round-trips, op by op
// ---------------------------------------------------------------------------

/** A document rich enough that every op in the taxonomy is applicable. */
function richDoc(): ProjectDoc {
  return applyGroup(makeTwoRoomPlanWithOpenings(), [
    {
      type: 'stair.add',
      payload: {
        id: FIXTURE_IDS.stair,
        storeyId: FIXTURE_IDS.groundStorey,
        kind: 'dogleg',
        origin: { x: 500, y: 500 },
        direction: 'N',
        riserMm: 167,
        treadMm: 275,
        widthMm: 1000,
        risersCount: 18,
        landing: { widthMm: 2115, depthMm: 1000 },
      },
    },
    {
      type: 'column.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.column,
        storeyId: FIXTURE_IDS.groundStorey,
        pt: { x: 3000, y: 2000 },
        sizeMm: { xMm: 230, yMm: 230 },
      },
    },
    {
      type: 'furniture.set',
      payload: {
        action: 'place',
        id: FIXTURE_IDS.sofa,
        storeyId: FIXTURE_IDS.groundStorey,
        catalogId: 'sofa-3seat-2100x900',
        pt: { x: 1500, y: 1500 },
        rotationDeg: 0,
      },
    },
    {
      type: 'balcony.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.balcony,
        storeyId: FIXTURE_IDS.groundStorey,
        polygon: rectPolygon(0, 4000, 2400, 4900),
        railingKind: 'ms',
        railingHeightMm: 1000,
        projectionMm: 900,
      },
    },
    {
      type: 'facade.apply_kit',
      payload: {
        kitId: 'contemporary',
        seed: 3,
        colorwayId: 'mono-wood',
        components: [
          {
            id: fixedId('facadecomp', 'CH1'),
            kind: 'chajja',
            storeyId: FIXTURE_IDS.groundStorey,
            wallId: FIXTURE_IDS.wallSouth,
            openingId: null,
            params: { projectionMm: 600 },
          },
        ],
      },
    },
    {
      type: 'material.assign',
      payload: {
        id: FIXTURE_IDS.material,
        target: { group: 'external_wall', storeyId: null, elementId: null },
        materialId: 'texture-paint-grey',
      },
    },
    {
      type: 'annotation.set',
      payload: {
        action: 'add',
        id: FIXTURE_IDS.annotation,
        sheetId: FIXTURE_IDS.sheet,
        anchorElementId: FIXTURE_IDS.wallSouth,
        anchorKind: 'wall',
        payload: { text: 'RCC beam over' },
      },
    },
  ]).model;
}

function expectRoundTrip(doc: ProjectDoc, op: Op, opts: { expectChange?: boolean } = {}): void {
  const before = docHash(doc);
  const { model, inverse } = fold(doc, op);
  if (opts.expectChange !== false) {
    expect(docHash(model), `${op.type} did not change the document`).not.toBe(before);
  }
  expect(inverse.length).toBeGreaterThan(0);
  const restored = applyGroup(model, inverse).model;
  expect(docHash(restored), `${op.type} inverse did not restore the document`).toBe(before);
}

describe('every op has a working inverse', () => {
  const base = richDoc();
  const roomId = base.house.rooms[0]!.id;

  const cases: [string, Op][] = [
    [
      'plot.set_boundary',
      { type: 'plot.set_boundary', payload: { polygon: rectPolygon(0, 0, 12000, 12000) } },
    ],
    ['plot.set_north', { type: 'plot.set_north', payload: { deg: 45 } }],
    ['plot.set_road (add)', { type: 'plot.set_road', payload: { edgeIndex: 1, widthMm: 6000 } }],
    ['plot.set_road (remove)', { type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: null } }],
    [
      'plot.set_reg_profile',
      { type: 'plot.set_reg_profile', payload: { cityPack: 'blr', overrides: { farMax: 175 } } },
    ],
    [
      'brief.update',
      { type: 'brief.update', payload: { patch: { bedrooms: 3 }, completeness: 40 } },
    ],
    [
      'storey.add',
      {
        type: 'storey.add',
        payload: { id: FIXTURE_IDS.firstStorey, index: 1, heightMm: 3000 },
      },
    ],
    ['storey.remove', { type: 'storey.remove', payload: { index: 0 } }],
    [
      'storey.set_height',
      // 3010, not a bigger jump: the base doc's stair is 18 × 167 = 3006mm,
      // and the op taxonomy says "stairs re-check" on a height change — a
      // height outside the ±10mm rise tolerance is REJECTED with
      // STAIR_RISE_MISMATCH (deliberately; the fix hint names the riser to
      // use). The round-trip needs a height the standing stair tolerates.
      {
        type: 'storey.set_height',
        payload: { storeyId: FIXTURE_IDS.groundStorey, heightMm: 3010 },
      },
    ],
    [
      'wall.add',
      {
        type: 'wall.add',
        payload: {
          id: fixedId('wall', 'NEW'),
          storeyId: FIXTURE_IDS.groundStorey,
          a: { x: 3000, y: 2000 },
          b: { x: 6000, y: 2000 },
          thicknessMm: 115,
          kind: 'internal',
        },
      },
    ],
    [
      'wall.move',
      {
        type: 'wall.move',
        payload: { wallId: FIXTURE_IDS.wallSpine, a: { x: 4000, y: 0 }, b: { x: 4000, y: 4000 } },
      },
    ],
    [
      'wall.split',
      {
        type: 'wall.split',
        payload: { wallId: FIXTURE_IDS.wallSouth, atMm: 3000, newWallId: fixedId('wall', 'NEW') },
      },
    ],
    ['wall.delete', { type: 'wall.delete', payload: { wallId: FIXTURE_IDS.wallSpine } }],
    [
      'wall.set_thickness',
      { type: 'wall.set_thickness', payload: { wallId: FIXTURE_IDS.wallSpine, thicknessMm: 230 } },
    ],
    [
      'opening.add',
      {
        type: 'opening.add',
        payload: {
          id: fixedId('opening', 'WNEW'),
          wallId: FIXTURE_IDS.wallEast,
          kind: 'window',
          widthMm: 1200,
          heightMm: 1200,
          sillMm: 900,
          offsetMm: 2000,
          swing: 'in-left',
        },
      },
    ],
    [
      'opening.move',
      { type: 'opening.move', payload: { openingId: FIXTURE_IDS.doorMain, offsetMm: 2500 } },
    ],
    [
      'opening.move (re-host)',
      {
        type: 'opening.move',
        payload: {
          openingId: FIXTURE_IDS.doorMain,
          offsetMm: 2000,
          wallId: FIXTURE_IDS.wallNorth,
        },
      },
    ],
    [
      'opening.resize',
      { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, widthMm: 1050 } },
    ],
    [
      'opening.flip',
      { type: 'opening.flip', payload: { openingId: FIXTURE_IDS.doorMain, swing: 'out-right' } },
    ],
    ['opening.delete', { type: 'opening.delete', payload: { openingId: FIXTURE_IDS.doorMain } }],
    [
      'room.assign',
      { type: 'room.assign', payload: { roomId, type: 'living', name: 'Living', locked: true } },
    ],
    [
      'room.set_target',
      { type: 'room.set_target', payload: { roomId, targetAreaMm2: 12_000_000, mustFace: 'NE' } },
    ],
    [
      'stair.add',
      {
        type: 'stair.add',
        payload: {
          id: fixedId('stair', 'ST2'),
          storeyId: FIXTURE_IDS.groundStorey,
          kind: 'straight',
          origin: { x: 4000, y: 500 },
          direction: 'N',
          riserMm: 167,
          treadMm: 275,
          widthMm: 900,
          risersCount: 18,
          landing: null,
        },
      },
    ],
    [
      'stair.edit',
      { type: 'stair.edit', payload: { stairId: FIXTURE_IDS.stair, patch: { widthMm: 1050 } } },
    ],
    ['stair.delete', { type: 'stair.delete', payload: { stairId: FIXTURE_IDS.stair } }],
    [
      'column.set (add)',
      {
        type: 'column.set',
        payload: {
          action: 'add',
          id: fixedId('column', 'C2'),
          storeyId: FIXTURE_IDS.groundStorey,
          pt: { x: 1000, y: 1000 },
        },
      },
    ],
    [
      'column.set (move)',
      {
        type: 'column.set',
        payload: { action: 'move', id: FIXTURE_IDS.column, pt: { x: 2000, y: 2000 } },
      },
    ],
    [
      'column.set (delete)',
      { type: 'column.set', payload: { action: 'delete', id: FIXTURE_IDS.column } },
    ],
    [
      'furniture.set (place)',
      {
        type: 'furniture.set',
        payload: {
          action: 'place',
          id: fixedId('furniture', 'F2'),
          storeyId: FIXTURE_IDS.groundStorey,
          catalogId: 'bed-queen-1900x1525',
          pt: { x: 4000, y: 2000 },
          rotationDeg: 90,
        },
      },
    ],
    [
      'furniture.set (transform)',
      {
        type: 'furniture.set',
        payload: {
          action: 'transform',
          id: FIXTURE_IDS.sofa,
          pt: { x: 1200, y: 1200 },
          rotationDeg: 180,
        },
      },
    ],
    [
      'furniture.set (delete)',
      { type: 'furniture.set', payload: { action: 'delete', id: FIXTURE_IDS.sofa } },
    ],
    [
      'balcony.set (add)',
      {
        type: 'balcony.set',
        payload: {
          action: 'add',
          id: fixedId('balcony', 'B2'),
          storeyId: FIXTURE_IDS.groundStorey,
          polygon: rectPolygon(3000, 4000, 5400, 4900),
        },
      },
    ],
    [
      'balcony.set (edit)',
      {
        type: 'balcony.set',
        payload: {
          action: 'edit',
          id: FIXTURE_IDS.balcony,
          railingKind: 'glass',
          projectionMm: 1200,
        },
      },
    ],
    [
      'balcony.set (delete)',
      { type: 'balcony.set', payload: { action: 'delete', id: FIXTURE_IDS.balcony } },
    ],
    [
      'facade.apply_kit',
      {
        type: 'facade.apply_kit',
        payload: { kitId: 'modern-minimal', seed: 11, colorwayId: 'white-grey', components: [] },
      },
    ],
    [
      'facade.apply_kit (clear)',
      { type: 'facade.apply_kit', payload: { kitId: null, seed: 0, components: [] } },
    ],
    [
      'facade.edit_component',
      {
        type: 'facade.edit_component',
        payload: { componentId: fixedId('facadecomp', 'CH1'), patch: { projectionMm: 750 } },
      },
    ],
    [
      'material.assign (change)',
      {
        type: 'material.assign',
        payload: {
          id: FIXTURE_IDS.material,
          target: { group: 'external_wall', storeyId: null, elementId: null },
          materialId: 'stone-cladding',
        },
      },
    ],
    [
      'material.assign (new)',
      {
        type: 'material.assign',
        payload: {
          id: fixedId('material', 'M2'),
          target: { group: 'floor', storeyId: null, elementId: null },
          materialId: 'vitrified-600',
        },
      },
    ],
    [
      'material.assign (clear)',
      {
        type: 'material.assign',
        payload: {
          id: FIXTURE_IDS.material,
          target: { group: 'external_wall', storeyId: null, elementId: null },
          materialId: null,
        },
      },
    ],
    ['levels.set', { type: 'levels.set', payload: { plinthMm: 750, parapetMm: 1050 } }],
    [
      'solver.apply_option',
      {
        type: 'solver.apply_option',
        payload: {
          solverJobId: 'job_test',
          optionIndex: 0,
          ops: [
            {
              type: 'wall.add',
              payload: {
                id: fixedId('wall', 'SV1'),
                storeyId: FIXTURE_IDS.groundStorey,
                a: { x: 0, y: 2000 },
                b: { x: 3000, y: 2000 },
                thicknessMm: 115,
                kind: 'internal',
              },
            },
            {
              type: 'wall.add',
              payload: {
                id: fixedId('wall', 'SV2'),
                storeyId: FIXTURE_IDS.groundStorey,
                a: { x: 3000, y: 2000 },
                b: { x: 6000, y: 2000 },
                thicknessMm: 115,
                kind: 'internal',
              },
            },
          ],
        },
      },
    ],
    [
      'annotation.set (add)',
      {
        type: 'annotation.set',
        payload: {
          action: 'add',
          id: fixedId('annotation', 'A2'),
          sheetId: FIXTURE_IDS.sheet,
          anchorElementId: FIXTURE_IDS.wallEast,
          anchorKind: 'wall',
          payload: { text: 'Lintel level 2100' },
        },
      },
    ],
    [
      'annotation.set (edit)',
      {
        type: 'annotation.set',
        payload: {
          action: 'edit',
          id: FIXTURE_IDS.annotation,
          payload: { text: 'Changed note' },
          orphaned: true,
        },
      },
    ],
    [
      'annotation.set (delete)',
      { type: 'annotation.set', payload: { action: 'delete', id: FIXTURE_IDS.annotation } },
    ],
  ];

  it.each(cases)('%s', (_name, op) => {
    expectRoundTrip(base, op);
  });

  it('covers every op type in the taxonomy', async () => {
    const { OP_TYPES } = await import('./ops');
    const covered = new Set(cases.map(([, op]) => op.type));
    expect([...OP_TYPES].filter((t) => !covered.has(t))).toEqual([]);
  });
});

describe('destructive inverses restore room metadata where the id survives', () => {
  it('restores a room name after the wall that split it is deleted and undone', () => {
    let doc = makeTwoRoomPlan();
    const [a, b] = doc.house.rooms;
    doc = applyGroup(doc, [
      { type: 'room.assign', payload: { roomId: a!.id, type: 'living', name: 'Living' } },
      { type: 'room.assign', payload: { roomId: b!.id, type: 'kitchen', name: 'Kitchen' } },
    ]).model;
    const before = docHash(doc);

    const { model: merged, inverse } = fold(doc, {
      type: 'wall.delete',
      payload: { wallId: FIXTURE_IDS.wallSpine },
    });
    expect(merged.house.rooms).toHaveLength(1);

    const restored = applyGroup(merged, inverse).model;
    expect(restored.house.rooms).toHaveLength(2);
    const names = restored.house.rooms.map((r) => r.name).sort();
    // the surviving room keeps its name; the merged-away one is restored by the
    // room.assign ops the inverse carries
    expect(names).toEqual(['Kitchen', 'Living']);
    expect(docHash(restored)).toBe(before);
  });
});

// ---------------------------------------------------------------------------
// groups, undo/redo
// ---------------------------------------------------------------------------

describe('applyGroup', () => {
  it('is atomic: a rejected op leaves the document untouched', () => {
    const doc = makeTwoRoomPlan();
    const before = docHash(doc);
    expect(() =>
      applyGroup(doc, [
        { type: 'plot.set_north', payload: { deg: 90 } },
        { type: 'wall.delete', payload: { wallId: fixedId('wall', 'NOPE') } },
      ]),
    ).toThrow();
    expect(docHash(doc)).toBe(before);
  });

  it('stamps the groupId on every applied op and on the inverse', () => {
    const groupId = 'grp_test' as GroupId;
    const result = applyGroup(
      makeTwoRoomPlan(),
      [
        { type: 'plot.set_north', payload: { deg: 90 } },
        { type: 'levels.set', payload: { plinthMm: 750 } },
      ],
      groupId,
    );
    expect(result.ops.every((o) => o.groupId === groupId)).toBe(true);
    expect(result.inverse.every((o) => o.groupId === groupId)).toBe(true);
  });

  it('inverts a group in reverse order', () => {
    const doc = makeTwoRoomPlan();
    const before = docHash(doc);
    const { model, inverse } = applyGroup(doc, [
      {
        type: 'wall.add',
        payload: {
          id: fixedId('wall', 'G1'),
          storeyId: FIXTURE_IDS.groundStorey,
          a: { x: 0, y: 2000 },
          b: { x: 3000, y: 2000 },
          thicknessMm: 115,
          kind: 'internal',
        },
      },
      {
        type: 'opening.add',
        payload: {
          id: fixedId('opening', 'G1'),
          wallId: fixedId('wall', 'G1'),
          kind: 'door',
          widthMm: 800,
          heightMm: 2100,
          sillMm: 0,
          offsetMm: 1500,
          swing: 'in-left',
        },
      },
    ]);
    expect(model.house.rooms).toHaveLength(3);
    expect(docHash(applyGroup(model, inverse).model)).toBe(before);
  });
});

describe('UndoStack', () => {
  function pushGroup(stack: UndoStack, doc: ProjectDoc, ops: Op[], label: string): ProjectDoc {
    const groupId = `grp_${String(stack.undoDepth)}` as GroupId;
    const result = applyGroup(doc, ops, groupId);
    stack.push({ groupId, ops: result.ops, inverse: result.inverse, label });
    return result.model;
  }

  it('undoes and redoes a group', () => {
    const stack = new UndoStack();
    const start = makeTwoRoomPlan();
    const startHash = docHash(start);
    const after = pushGroup(
      stack,
      start,
      [{ type: 'wall.delete', payload: { wallId: FIXTURE_IDS.wallSpine } }],
      'Wall deleted',
    );
    const afterHash = docHash(after);
    expect(stack.canUndo).toBe(true);
    expect(stack.nextUndoLabel).toBe('Wall deleted');

    const undone = stack.undo(after);
    expect(undone).not.toBeNull();
    expect(docHash(undone!.model)).toBe(startHash);
    expect(stack.canUndo).toBe(false);
    expect(stack.canRedo).toBe(true);

    const redone = stack.redo(undone!.model);
    expect(docHash(redone!.model)).toBe(afterHash);
    expect(stack.canUndo).toBe(true);
  });

  it('returns null when there is nothing to undo or redo', () => {
    const stack = new UndoStack();
    expect(stack.undo(makeEmptyDoc())).toBeNull();
    expect(stack.redo(makeEmptyDoc())).toBeNull();
  });

  it('drops the redo stack when new work lands', () => {
    const stack = new UndoStack();
    let doc = makeTwoRoomPlan();
    doc = pushGroup(stack, doc, [{ type: 'plot.set_north', payload: { deg: 30 } }], 'North');
    doc = stack.undo(doc)!.model;
    expect(stack.canRedo).toBe(true);
    pushGroup(stack, doc, [{ type: 'plot.set_north', payload: { deg: 60 } }], 'North again');
    expect(stack.canRedo).toBe(false);
  });

  it('round-trips 200 pseudo-random ops and back', () => {
    const stack = new UndoStack(500);
    let doc = makeTwoRoomPlanWithOpenings();
    const hashes: string[] = [docHash(doc)];

    // deterministic LCG so a failure is reproducible
    let seed = 20260731;
    const rand = (n: number): number => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed % n;
    };

    for (let i = 0; i < 200; i++) {
      const rooms = doc.house.rooms;
      const choice = rand(6);
      let ops: Op[];
      switch (choice) {
        case 0: {
          // stay in a band where each room keeps a clear best match, so the
          // test exercises id preservation rather than id re-assignment
          const x = 2540 + rand(9) * 115;
          ops = [
            {
              type: 'wall.move',
              payload: { wallId: FIXTURE_IDS.wallSpine, a: { x, y: 0 }, b: { x, y: 4000 } },
            },
          ];
          break;
        }
        case 1:
          ops = [
            {
              type: 'wall.set_thickness',
              payload: {
                wallId: FIXTURE_IDS.wallSpine,
                thicknessMm: [115, 150, 230][rand(3)]!,
              },
            },
          ];
          break;
        case 2:
          ops = [
            {
              type: 'room.assign',
              payload: {
                roomId: rooms[rand(rooms.length)]!.id,
                type: ROOM_TYPES[rand(ROOM_TYPES.length)] as RoomType,
              },
            },
          ];
          break;
        case 3:
          ops = [
            {
              type: 'opening.move',
              payload: { openingId: FIXTURE_IDS.doorMain, offsetMm: 1000 + rand(1000) },
            },
          ];
          break;
        case 4:
          ops = [
            {
              type: 'opening.resize',
              payload: { openingId: FIXTURE_IDS.doorMain, widthMm: [750, 900, 1050][rand(3)]! },
            },
          ];
          break;
        default:
          ops = [{ type: 'levels.set', payload: { plinthMm: 450 + rand(4) * 150 } }];
          break;
      }
      const groupId = `grp_${String(i)}` as GroupId;
      const result = applyGroup(doc, ops, groupId);
      stack.push({ groupId, ops: result.ops, inverse: result.inverse });
      doc = result.model;
      hashes.push(docHash(doc));
    }

    // undo everything, checking each intermediate state on the way back
    for (let i = 200; i > 0; i--) {
      expect(docHash(doc)).toBe(hashes[i]);
      doc = stack.undo(doc)!.model;
    }
    expect(docHash(doc)).toBe(hashes[0]);

    // redo everything
    for (let i = 1; i <= 200; i++) {
      doc = stack.redo(doc)!.model;
      expect(docHash(doc)).toBe(hashes[i]);
    }
  });

  it('serialises and restores its history', () => {
    const stack = new UndoStack();
    const doc = applyGroup(makeTwoRoomPlan(), [{ type: 'plot.set_north', payload: { deg: 15 } }]);
    stack.push({ groupId: 'g1', ops: doc.ops, inverse: doc.inverse, label: 'North' });
    const restored = UndoStack.fromJSON(stack.toJSON());
    expect(restored.undoDepth).toBe(1);
    expect(restored.nextUndoLabel).toBe('North');
  });
});

// ---------------------------------------------------------------------------
// derived reads
// ---------------------------------------------------------------------------

describe('derived reads', () => {
  it('measures walls, carpet area and built-up area', () => {
    const doc = makeTwoRoomPlan();
    const south = doc.house.walls.find((w) => w.id === FIXTURE_IDS.wallSouth);
    expect(wallLengthMm(south!)).toBe(6000);
    expect(storeyCarpetAreaMm2(doc, FIXTURE_IDS.groundStorey)).toBe(2 * 2828 * 3770);
    expect(storeyBuiltUpAreaMm2(doc, FIXTURE_IDS.groundStorey)).toBe(6230 * 4230);
  });

  it('lists locked rooms for solver re-solve', () => {
    const doc = makeTwoRoomPlan();
    const locked = fold(doc, {
      type: 'room.assign',
      payload: { roomId: doc.house.rooms[0]!.id, type: 'living', locked: true },
    }).model;
    expect(lockedRoomIds(locked)).toEqual([doc.house.rooms[0]!.id]);
  });

  it('computes a stair footprint for slab cut-outs', () => {
    const straight = stairFootprintPolygon({
      id: FIXTURE_IDS.stair,
      storeyId: FIXTURE_IDS.groundStorey,
      kind: 'straight',
      origin: { x: 1000, y: 1000 },
      direction: 'N',
      riserMm: 167,
      treadMm: 275,
      widthMm: 900,
      risersCount: 18,
      landing: null,
    });
    // 17 goings x 275 = 4675 deep, 900 wide, heading north from (1000,1000)
    expect(straight).toEqual(rectPolygon(1000, 1000, 1900, 5675));
    expect(polygonAreaMm2(straight)).toBe(900 * 4675);
  });

  it('cuts the stair well out of the slab above', () => {
    const twoStorey = applyGroup(makeTwoRoomPlan(), [
      { type: 'storey.add', payload: { id: FIXTURE_IDS.firstStorey, index: 1, heightMm: 3000 } },
      {
        type: 'stair.add',
        payload: {
          id: FIXTURE_IDS.stair,
          storeyId: FIXTURE_IDS.groundStorey,
          kind: 'straight',
          origin: { x: 500, y: 500 },
          direction: 'N',
          riserMm: 167,
          treadMm: 275,
          widthMm: 900,
          risersCount: 18,
          landing: null,
        },
      },
      {
        type: 'wall.add',
        payload: {
          id: fixedId('wall', 'UF1'),
          storeyId: FIXTURE_IDS.firstStorey,
          a: { x: 0, y: 0 },
          b: { x: 6000, y: 0 },
          thicknessMm: 230,
          kind: 'external',
        },
      },
    ]).model;
    const upperSlab = twoStorey.house.slabs.find((s) => s.storeyId === FIXTURE_IDS.firstStorey);
    // the upper storey has only one wall, so there is no enclosed outline yet
    expect(upperSlab).toBeUndefined();
    const groundSlab = twoStorey.house.slabs.find((s) => s.storeyId === FIXTURE_IDS.groundStorey);
    expect(groundSlab?.cutouts).toEqual([]);
  });
});

describe('tryFold', () => {
  it('returns issues instead of throwing (the copilot dry-run path)', () => {
    const outcome = tryFold(makeTwoRoomPlan(), {
      type: 'wall.delete',
      payload: { wallId: fixedId('wall', 'NOPE') },
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.issues[0]?.code).toBe('WALL_UNKNOWN');
  });

  it('returns the folded model on success', () => {
    const outcome = tryFold(makeTwoRoomPlan(), { type: 'plot.set_north', payload: { deg: 12 } });
    expect(outcome.ok).toBe(true);
    if (outcome.ok) expect(outcome.model.plot.northDeg).toBe(12);
  });
});

// ---------------------------------------------------------------------------
// THE CROSS-LANGUAGE GOLDEN CONTRACT
//
// fixtures/model/golden-states.json is generated by
// fixtures/model/_tools/generate_golden_states.py from the PYTHON mirror. This
// block is the TypeScript half of that contract, and it is the only thing in the
// repo that can catch the two implementations disagreeing about what a design IS.
//
// A failure here is never fixed by pasting the new hash. `stateHash` is what
// `design_versions.snapshot_hash` stores and what the 409-rebase path compares:
// if the two languages disagree, sync is broken, not the fixture. Find out which
// side moved, then regenerate with
//     python3 fixtures/model/_tools/generate_golden_states.py
// in the same commit, with a note.
// ---------------------------------------------------------------------------

interface GoldenStateCase {
  readonly name: string;
  readonly description: string;
  readonly unitsDisplay: 'ft-in' | 'm';
  readonly ops: readonly Op[];
  readonly expectedStateHash: string;
}

interface GoldenStatesFile {
  readonly schemaVersion: number;
  readonly canonicalJsonSpec: string;
  readonly hashAlgorithm: string;
  readonly cases: readonly GoldenStateCase[];
}

function loadGoldenStates(): GoldenStatesFile {
  const url = new URL('../../../fixtures/model/golden-states.json', import.meta.url);
  return JSON.parse(readFileSync(url, 'utf8')) as GoldenStatesFile;
}

describe('golden states (fixtures/model/golden-states.json)', () => {
  const golden = loadGoldenStates();

  it('declares the canonical form this package implements', () => {
    expect(golden.canonicalJsonSpec).toBe(CANONICAL_JSON_SPEC);
    expect(golden.hashAlgorithm).toBe(STATE_HASH_ALGORITHM);
    expect(golden.cases.length).toBeGreaterThanOrEqual(11);
  });

  it.each(golden.cases.map((c) => [c.name, c] as const))(
    'folds %s to the hash the Python mirror produced',
    (_name: string, testCase: GoldenStateCase) => {
      const doc = replay(testCase.ops, emptyProjectDoc(testCase.unitsDisplay));
      expect(stateHash(doc)).toBe(testCase.expectedStateHash);
      // docHash is defined as stateHash over the whole ProjectDoc; pin that too so
      // the two entry points cannot drift apart.
      expect(docHash(doc)).toBe(testCase.expectedStateHash);
    },
  );

  it.each(golden.cases.map((c) => [c.name, c] as const))(
    'replays %s deterministically, op by op, to the same hash',
    (_name: string, testCase: GoldenStateCase) => {
      // Fold one op at a time through `fold` (which validates and computes an
      // inverse) rather than through `replay` (which skips the inverse). Both
      // paths must land on the same document, or the inverse machinery is
      // mutating state it should only observe.
      let doc = emptyProjectDoc(testCase.unitsDisplay);
      for (const op of testCase.ops) doc = fold(doc, op).model;
      expect(stateHash(doc)).toBe(testCase.expectedStateHash);
    },
  );

  it('every golden hash is 64 lowercase hex characters', () => {
    for (const testCase of golden.cases) {
      expect(testCase.expectedStateHash).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it('undoes every golden op log back to the empty document', () => {
    for (const testCase of golden.cases) {
      const empty = emptyProjectDoc(testCase.unitsDisplay);
      const emptyHash = stateHash(empty);
      const stack = new UndoStack();
      let doc = empty;
      for (const op of testCase.ops) {
        const result = fold(doc, op);
        doc = result.model;
        stack.push({ groupId: `g-${testCase.name}`, ops: [op], inverse: result.inverse });
      }
      let undone: ProjectDoc = doc;
      for (;;) {
        const step = stack.undo(undone);
        if (step === null) break;
        undone = step.model;
      }
      // Known, documented gap: a room named with `room.assign` whose geometry is
      // later destroyed cannot be un-named by id (see the model-core hand-off,
      // finding C9). No golden case exercises that path, so all of them must
      // round-trip exactly.
      expect(stateHash(undone)).toBe(emptyHash);
    }
  });
});
