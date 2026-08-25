import { describe, expect, it } from 'vitest';

import { fold, tryFold } from './fold';
import { DEFAULTS } from './model';
import type { Op } from './ops';
import { FIXTURE_IDS, fixedId, makeEmptyDoc, makeTwoRoomPlan } from './testing';
import {
  MODEL_INVARIANT_CODES,
  OpRejectedError,
  VALIDATION_CODES,
  isAcceptable,
  issuesByCode,
  renderIssuesForLlm,
  validateModel,
  validateOpShape,
} from './validate';
import type { ValidationCode } from './validate';

/** Every rejection asserted through the same door the copilot uses. */
function reject(op: Op, doc = makeTwoRoomPlan()): ValidationCode[] {
  const outcome = tryFold(doc, op);
  expect(outcome.ok, `expected ${op.type} to be rejected`).toBe(false);
  return outcome.ok ? [] : outcome.issues.map((i) => i.code);
}

describe('code list hygiene', () => {
  it('has no duplicate codes', () => {
    expect(new Set(VALIDATION_CODES).size).toBe(VALIDATION_CODES.length);
  });

  it('lists model-invariant codes that all exist', () => {
    for (const code of MODEL_INVARIANT_CODES) {
      expect(VALIDATION_CODES).toContain(code);
    }
  });
});

describe('op shape validation', () => {
  it('rejects unknown op types', () => {
    const issues = validateOpShape({ type: 'wall.teleport', payload: {} });
    expect(issues.map((i) => i.code)).toEqual(['OP_UNKNOWN_TYPE']);
  });

  it('rejects a non-object op or payload', () => {
    expect(validateOpShape('nope')[0]?.code).toBe('OP_PAYLOAD_NOT_OBJECT');
    expect(validateOpShape({ type: 'wall.delete', payload: 3 })[0]?.code).toBe(
      'OP_PAYLOAD_NOT_OBJECT',
    );
  });

  it('rejects float millimetres — the whole point of the model', () => {
    const issues = validateOpShape({
      type: 'wall.add',
      payload: {
        id: FIXTURE_IDS.wallSpine,
        storeyId: FIXTURE_IDS.groundStorey,
        a: { x: 0, y: 0 },
        b: { x: 3000.5, y: 0 },
        thicknessMm: 115,
        kind: 'internal',
      },
    });
    expect(issues.some((i) => i.code === 'OP_FIELD_BAD_POINT')).toBe(true);
  });

  it('rejects floats hiding in free-form brief JSON', () => {
    const issues = validateOpShape({
      type: 'brief.update',
      payload: { patch: { budget: { perSqft: 1850.5 } } },
    });
    expect(issues.map((i) => i.code)).toContain('OP_FIELD_NOT_INT');
    expect(issues[0]?.field).toBe('payload.patch.budget.perSqft');
  });

  it('names the missing field', () => {
    const issues = validateOpShape({ type: 'wall.delete', payload: {} });
    expect(issues[0]?.code).toBe('OP_FIELD_MISSING');
    expect(issues[0]?.field).toBe('payload.wallId');
    expect(issues[0]?.fix).toBeDefined();
  });

  it('rejects an id of the wrong namespace', () => {
    const issues = validateOpShape({
      type: 'wall.delete',
      payload: { wallId: FIXTURE_IDS.doorMain },
    });
    expect(issues[0]?.code).toBe('OP_FIELD_BAD_ID');
    expect(issues[0]?.limit).toBe('wall_<ulid>');
  });

  it('rejects a bad enum with the legal values in the message', () => {
    const issues = validateOpShape({
      type: 'opening.flip',
      payload: { openingId: FIXTURE_IDS.doorMain, swing: 'sideways' },
    });
    expect(issues[0]?.code).toBe('OP_FIELD_BAD_ENUM');
    expect(issues[0]?.message).toContain('in-left');
  });

  it('rejects a self-intersecting polygon', () => {
    const issues = validateOpShape({
      type: 'plot.set_boundary',
      payload: {
        polygon: [
          { x: 0, y: 0 },
          { x: 1000, y: 1000 },
          { x: 1000, y: 0 },
          { x: 0, y: 1000 },
        ],
      },
    });
    expect(issues[0]?.code).toBe('OP_FIELD_BAD_POLYGON');
  });

  it('accepts an empty boundary polygon (the clear/undo form)', () => {
    expect(validateOpShape({ type: 'plot.set_boundary', payload: { polygon: [] } })).toEqual([]);
  });

  it('requires at least one field on opening.resize and levels.set', () => {
    expect(
      validateOpShape({ type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain } })[0]
        ?.code,
    ).toBe('OP_FIELD_MISSING');
    expect(validateOpShape({ type: 'levels.set', payload: {} })[0]?.code).toBe('OP_FIELD_MISSING');
  });
});

describe('the §3 fold invariants', () => {
  it('walls have non-zero length', () => {
    expect(
      reject({
        type: 'wall.add',
        payload: {
          id: fixedId('wall', 'ZZ'),
          storeyId: FIXTURE_IDS.groundStorey,
          a: { x: 1000, y: 1000 },
          b: { x: 1000, y: 1000 },
          thicknessMm: 115,
          kind: 'internal',
        },
      }),
    ).toContain('WALL_ZERO_LENGTH');
  });

  it('no two walls exactly overlap', () => {
    expect(
      reject({
        type: 'wall.add',
        payload: {
          id: fixedId('wall', 'ZZ'),
          storeyId: FIXTURE_IDS.groundStorey,
          a: { x: 1000, y: 0 },
          b: { x: 5000, y: 0 },
          thicknessMm: 230,
          kind: 'external',
        },
      }),
    ).toContain('WALL_DUPLICATE');
  });

  it('openings fit within the host wall minus 115mm end margins', () => {
    // the south wall is 6000 long, so a 6000-wide door cannot fit
    const tooWide = reject({
      type: 'opening.add',
      payload: {
        id: fixedId('opening', 'DZ'),
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: 6000,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 3000,
        swing: 'in-left',
      },
    });
    expect(tooWide).toContain('OPENING_OUT_OF_WALL');

    const tooCloseToTheEnd = reject({
      type: 'opening.add',
      payload: {
        id: fixedId('opening', 'DZ'),
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: 900,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 400, // 400 - 450 = -50 < 115mm margin
        swing: 'in-left',
      },
    });
    expect(tooCloseToTheEnd).toContain('OPENING_OUT_OF_WALL');
  });

  it('suggests the legal offset range when an opening does not fit', () => {
    const outcome = tryFold(makeTwoRoomPlan(), {
      type: 'opening.add',
      payload: {
        id: fixedId('opening', 'DZ'),
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: 900,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 400,
        swing: 'in-left',
      },
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      const issue = outcome.issues[0];
      expect(issue?.limit).toBe('565..5435');
      expect(issue?.fix).toContain('565');
    }
  });

  it('opening sill + height must fit the storey height', () => {
    expect(
      reject({
        type: 'opening.add',
        payload: {
          id: fixedId('opening', 'WZ'),
          wallId: FIXTURE_IDS.wallSouth,
          kind: 'window',
          widthMm: 1200,
          heightMm: 2400,
          sillMm: 900, // 3300 > 3000 storey
          offsetMm: 3000,
          swing: 'in-left',
        },
      }),
    ).toContain('OPENING_EXCEEDS_STOREY_HEIGHT');
  });

  it('stair rise must match the storey height within ±10mm', () => {
    const bad = reject({
      type: 'stair.add',
      payload: {
        id: FIXTURE_IDS.stair,
        storeyId: FIXTURE_IDS.groundStorey,
        kind: 'dogleg',
        origin: { x: 1000, y: 1000 },
        direction: 'N',
        riserMm: 150, // 18 x 150 = 2700, storey is 3000
        treadMm: 275,
        widthMm: 1000,
        risersCount: 18,
        landing: null,
      },
    });
    expect(bad).toContain('STAIR_RISE_MISMATCH');

    // 18 x 167 = 3006, within tolerance
    const ok = tryFold(makeTwoRoomPlan(), {
      type: 'stair.add',
      payload: {
        id: FIXTURE_IDS.stair,
        storeyId: FIXTURE_IDS.groundStorey,
        kind: 'dogleg',
        origin: { x: 1000, y: 1000 },
        direction: 'N',
        riserMm: 167,
        treadMm: 275,
        widthMm: 1000,
        risersCount: 18,
        landing: null,
      },
    });
    expect(ok.ok).toBe(true);
  });

  it('suggests a riser height that would work', () => {
    const outcome = tryFold(makeTwoRoomPlan(), {
      type: 'stair.add',
      payload: {
        id: FIXTURE_IDS.stair,
        storeyId: FIXTURE_IDS.groundStorey,
        kind: 'straight',
        origin: { x: 1000, y: 1000 },
        direction: 'N',
        riserMm: 150,
        treadMm: 275,
        widthMm: 1000,
        risersCount: 18,
        landing: null,
      },
    });
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.issues[0]?.fix).toContain('167');
  });

  it('rooms must be closed', () => {
    const doc = makeTwoRoomPlan();
    const broken = {
      ...doc,
      house: {
        ...doc.house,
        rooms: doc.house.rooms.map((r, i) =>
          i === 0 ? { ...r, polygon: [r.polygon[0]!, r.polygon[1]!] } : r,
        ),
      },
    };
    const codes = validateModel(broken).map((i) => i.code);
    expect(codes).toContain('ROOM_NOT_CLOSED');
  });

  it('flags duplicate element ids', () => {
    const doc = makeTwoRoomPlan();
    const wall = doc.house.walls[0]!;
    const dup = { ...doc, house: { ...doc.house, walls: [...doc.house.walls, wall] } };
    expect(validateModel(dup).map((i) => i.code)).toContain('DUPLICATE_ELEMENT_ID');
  });

  it('accepts the two-room fixture', () => {
    const issues = validateModel(makeTwoRoomPlan());
    expect(issues, JSON.stringify(issues)).toEqual([]);
    expect(isAcceptable(issues)).toBe(true);
  });
});

describe('document preconditions', () => {
  it('rejects references to elements that do not exist', () => {
    expect(reject({ type: 'wall.delete', payload: { wallId: fixedId('wall', 'NOPE') } })).toContain(
      'WALL_UNKNOWN',
    );
    expect(
      reject({ type: 'opening.delete', payload: { openingId: fixedId('opening', 'NOPE') } }),
    ).toContain('OPENING_UNKNOWN');
    expect(
      reject({ type: 'room.assign', payload: { roomId: fixedId('room', 'NOPE'), type: 'bedroom' } }),
    ).toContain('ROOM_UNKNOWN');
    expect(
      reject({ type: 'stair.delete', payload: { stairId: fixedId('stair', 'NOPE') } }),
    ).toContain('STAIR_UNKNOWN');
  });

  it('rejects re-using an id that is already taken', () => {
    expect(
      reject({
        type: 'wall.add',
        payload: {
          id: FIXTURE_IDS.wallSpine,
          storeyId: FIXTURE_IDS.groundStorey,
          a: { x: 1000, y: 500 },
          b: { x: 2000, y: 500 },
          thicknessMm: 115,
          kind: 'internal',
        },
      }),
    ).toContain('OP_ID_ALREADY_EXISTS');
  });

  it('rejects a storey index out of range', () => {
    expect(reject({ type: 'storey.remove', payload: { index: 4 } })).toContain(
      'STOREY_INDEX_OUT_OF_RANGE',
    );
    expect(
      reject({
        type: 'storey.add',
        payload: { id: FIXTURE_IDS.firstStorey, index: 7, heightMm: DEFAULTS.storeyHeightMm },
      }),
    ).toContain('STOREY_INDEX_OUT_OF_RANGE');
  });

  it('rejects a road on an edge the plot does not have', () => {
    expect(reject({ type: 'plot.set_road', payload: { edgeIndex: 9, widthMm: 9000 } })).toContain(
      'PLOT_EDGE_UNKNOWN',
    );
  });

  it('asks for the plot boundary before roads', () => {
    expect(
      reject({ type: 'plot.set_road', payload: { edgeIndex: 0, widthMm: 9000 } }, makeEmptyDoc()),
    ).toContain('PLOT_BOUNDARY_NOT_CLOSED');
  });

  it('rejects splitting a wall outside its length', () => {
    expect(
      reject({
        type: 'wall.split',
        payload: {
          wallId: FIXTURE_IDS.wallSouth,
          atMm: 9000,
          newWallId: fixedId('wall', 'NEW'),
        },
      }),
    ).toContain('WALL_SPLIT_OUT_OF_RANGE');
  });

  it('rejects moving a wall so that it overlaps another', () => {
    expect(
      reject({
        type: 'wall.move',
        payload: {
          wallId: FIXTURE_IDS.wallSpine,
          a: { x: 0, y: 0 },
          b: { x: 6000, y: 0 },
        },
      }),
    ).toContain('WALL_DUPLICATE');
  });

  it('rejects moving a wall so short that its opening no longer fits', () => {
    const doc = fold(makeTwoRoomPlan(), {
      type: 'opening.add',
      payload: {
        id: FIXTURE_IDS.doorMain,
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: 900,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 1500,
        swing: 'in-left',
      },
    }).model;
    const codes = reject(
      {
        type: 'wall.move',
        payload: {
          wallId: FIXTURE_IDS.wallSouth,
          a: { x: 0, y: 0 },
          b: { x: 1000, y: 0 },
        },
      },
      doc,
    );
    expect(codes).toContain('OPENING_OUT_OF_WALL');
  });
});

describe('rejection reasons are usable by the copilot', () => {
  it('carries code, field, actual, limit and a fix', () => {
    const outcome = tryFold(makeTwoRoomPlan(), {
      type: 'opening.add',
      payload: {
        id: fixedId('opening', 'DZ'),
        wallId: FIXTURE_IDS.wallSouth,
        kind: 'door',
        widthMm: 6000,
        heightMm: 2100,
        sillMm: 0,
        offsetMm: 3000,
        swing: 'in-left',
      },
    });
    expect(outcome.ok).toBe(false);
    if (outcome.ok) return;
    const issue = outcome.issues[0];
    expect(issue?.code).toBe('OPENING_OUT_OF_WALL');
    expect(issue?.field).toBe('payload.widthMm');
    expect(issue?.actual).toBe(6000);
    expect(issue?.limit).toBe(5770);
    expect(issue?.fix).toContain('5770');
    expect(issue?.elementIds).toHaveLength(1);
  });

  it('renders one compact line per issue for the self-correction pass', () => {
    const issues = validateOpShape({ type: 'wall.delete', payload: {} });
    const rendered = renderIssuesForLlm(issues);
    expect(rendered).toContain('OP_FIELD_MISSING');
    expect(rendered).toContain('field=payload.wallId');
    expect(rendered.split('\n')).toHaveLength(issues.length);
  });

  it('groups issues by code for the compliance strip', () => {
    const grouped = issuesByCode(validateOpShape({ type: 'wall.delete', payload: {} }));
    expect(grouped.get('OP_FIELD_MISSING')).toHaveLength(1);
  });

  it('throws OpRejectedError from fold, naming the op and keeping the issues', () => {
    try {
      fold(makeTwoRoomPlan(), { type: 'wall.delete', payload: { wallId: fixedId('wall', 'NOPE') } });
      expect.unreachable('fold should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(OpRejectedError);
      const err = e as OpRejectedError;
      expect(err.opType).toBe('wall.delete');
      expect(err.issues[0]?.code).toBe('WALL_UNKNOWN');
      expect(err.message).toContain('WALL_UNKNOWN');
    }
  });
});
