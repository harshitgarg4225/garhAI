/**
 * Spec: selection → editable fields → ops.
 *
 * Two things are being pinned here. The first is that every editable field
 * produces an op group the REAL model core accepts — so the specs fold what the
 * inspector builds rather than trusting its shape. The second is that fields
 * with no op behind them are honestly marked read-only, because a control that
 * looks editable and silently does nothing is worse than one that explains why.
 */

import { describe, expect, it } from 'vitest';

import { applyGroup, FIXTURE_IDS, makeTwoRoomPlanWithOpenings } from '@garh/model';

import { inspectorSelection, type InspectorField } from './fields';

const doc = makeTwoRoomPlanWithOpenings();
const house = doc.house;
const display = 'ft-in' as const;

function fieldOf(fields: readonly InspectorField[], key: string): InspectorField {
  const found = fields.find((f) => f.key === key);
  if (found === undefined) throw new Error(`no field ${key}`);
  return found;
}

// ---------------------------------------------------------------------------
// Selection shapes
// ---------------------------------------------------------------------------

describe('inspectorSelection', () => {
  it('says nothing is selected rather than rendering an empty panel', () => {
    const s = inspectorSelection(house, [], { display });
    expect(s.kind).toBe('none');
    expect(s.fields).toEqual([]);
  });

  it('refuses to guess what a mixed selection has in common', () => {
    const s = inspectorSelection(house, [FIXTURE_IDS.wallSouth, FIXTURE_IDS.doorMain], { display });
    expect(s.kind).toBe('mixed');
    expect(s.fields).toEqual([]);
    expect(s.subtitle).toMatch(/one kind/i);
  });

  it('says so when the selection has been pruned out from under it', () => {
    const s = inspectorSelection(house, ['wall_01J0000000000000000000GON'], { display });
    expect(s.count).toBe(0);
    expect(s.title).toMatch(/gone/i);
  });
});

// ---------------------------------------------------------------------------
// Wall
// ---------------------------------------------------------------------------

describe('wall inspector', () => {
  const single = inspectorSelection(house, [FIXTURE_IDS.wallSouth], { display });

  it('offers length and thickness, and folds both', () => {
    const length = fieldOf(single.fields, 'length');
    expect(length.value).toBe(6000);
    const ops = length.build(4000);
    const next = applyGroup(doc, ops, 'g').model;
    const wall = next.house.walls.find((w) => w.id === FIXTURE_IDS.wallSouth);
    // The near end stays put; the far end comes in.
    expect(wall?.a).toEqual({ x: 0, y: 0 });
    expect(wall?.b).toEqual({ x: 4000, y: 0 });

    const thickness = fieldOf(single.fields, 'thickness');
    expect(thickness.value).toBe(230);
    const thickened = applyGroup(doc, thickness.build(115), 'g2').model;
    expect(thickened.house.walls.find((w) => w.id === FIXTURE_IDS.wallSouth)?.thicknessMm).toBe(115);
  });

  it('marks wall kind and load-bearing read-only, with the reason', () => {
    // There is no `wall.set_kind` op in the §4 taxonomy and the inspector does
    // not get to invent one.
    const kind = fieldOf(single.fields, 'kind');
    expect(kind.editable).toBe(false);
    expect(kind.reason).toBeTruthy();
    expect(kind.build('internal')).toEqual([]);
    expect(fieldOf(single.fields, 'loadBearing').editable).toBe(false);
  });

  it('will not re-length several walls at once', () => {
    const many = inspectorSelection(house, [FIXTURE_IDS.wallSouth, FIXTURE_IDS.wallNorth], {
      display,
    });
    const length = fieldOf(many.fields, 'length');
    expect(length.editable).toBe(false);
    expect(length.reason).toMatch(/one wall/i);
  });

  it('thickens a multi-selection as ONE group, skipping walls already right', () => {
    const many = inspectorSelection(
      house,
      [FIXTURE_IDS.wallSouth, FIXTURE_IDS.wallNorth, FIXTURE_IDS.wallSpine],
      { display },
    );
    const thickness = fieldOf(many.fields, 'thickness');
    expect(thickness.mixed).toBe(true);
    const ops = thickness.build(230);
    // The spine is 115; the other two are already 230 and produce no op.
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ payload: { wallId: FIXTURE_IDS.wallSpine, thicknessMm: 230 } });
    const next = applyGroup(doc, ops, 'g3').model;
    expect(next.house.walls.every((w) => w.thicknessMm === 230)).toBe(true);
  });

  it('offers delete, and it folds', () => {
    const action = single.actions.find((a) => a.key === 'delete');
    expect(action?.tone).toBe('danger');
    const next = applyGroup(doc, action?.ops ?? [], 'g4').model;
    expect(next.house.walls.some((w) => w.id === FIXTURE_IDS.wallSouth)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Opening
// ---------------------------------------------------------------------------

describe('opening inspector', () => {
  const s = inspectorSelection(house, [FIXTURE_IDS.doorMain], { display });

  it('writes each dimension into its own payload key', () => {
    expect(fieldOf(s.fields, 'width').build(1000)).toEqual([
      { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, widthMm: 1000 } },
    ]);
    expect(fieldOf(s.fields, 'height').build(2400)).toEqual([
      { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, heightMm: 2400 } },
    ]);
    expect(fieldOf(s.fields, 'sill').build(900)).toEqual([
      { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, sillMm: 900 } },
    ]);
  });

  it('moves rather than resizes when the position changes', () => {
    expect(fieldOf(s.fields, 'offset').build(2000)).toEqual([
      { type: 'opening.move', payload: { openingId: FIXTURE_IDS.doorMain, offsetMm: 2000 } },
    ]);
  });

  it('flips the swing through the op meant for it', () => {
    const ops = fieldOf(s.fields, 'swing').build('out-right');
    expect(ops).toEqual([
      { type: 'opening.flip', payload: { openingId: FIXTURE_IDS.doorMain, swing: 'out-right' } },
    ]);
    const next = applyGroup(doc, ops, 'g5').model;
    expect(next.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain)?.swing).toBe('out-right');
  });

  it('leaves the schedule tag to the schedule generator', () => {
    expect(fieldOf(s.fields, 'tag').editable).toBe(false);
  });

  it('produces no op when the value has not changed', () => {
    expect(fieldOf(s.fields, 'width').build(900)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Room
// ---------------------------------------------------------------------------

describe('room inspector', () => {
  const room = house.rooms[0];

  it('separates the measured area from the target area', () => {
    expect(room).toBeDefined();
    if (room === undefined) return;
    const s = inspectorSelection(house, [room.id], { display });

    // Measured: read-only, because it is a consequence of the walls.
    const area = fieldOf(s.fields, 'area');
    expect(area.editable).toBe(false);
    expect(area.reason).toMatch(/walls/i);

    // Target: editable, and it is a different op and a different number.
    const target = fieldOf(s.fields, 'targetArea');
    expect(target.editable).toBe(true);
    expect(target.build(12_000_000)).toEqual([
      { type: 'room.set_target', payload: { roomId: room.id, targetAreaMm2: 12_000_000 } },
    ]);
  });

  it('keeps the type when only the name changes, and vice versa', () => {
    expect(room).toBeDefined();
    if (room === undefined) return;
    const s = inspectorSelection(house, [room.id], { display });

    const renamed = applyGroup(doc, fieldOf(s.fields, 'name').build("Ma's room"), 'g6').model;
    const after = renamed.house.rooms.find((r) => r.id === room.id);
    expect(after?.name).toBe("Ma's room");
    expect(after?.type).toBe(room.type);

    const retyped = applyGroup(doc, fieldOf(s.fields, 'type').build('bedroom_master'), 'g7').model;
    expect(retyped.house.rooms.find((r) => r.id === room.id)?.type).toBe('bedroom_master');
  });

  it('clears a facing requirement with null, not an empty string', () => {
    expect(room).toBeDefined();
    if (room === undefined) return;

    // Give the room a facing first — clearing something that is already clear
    // correctly produces no op, so the empty case cannot test the null.
    const faced = applyGroup(
      doc,
      [{ type: 'room.set_target', payload: { roomId: room.id, mustFace: 'NE' } }],
      'g-face',
    ).model;
    const s = inspectorSelection(faced.house, [room.id], { display });
    expect(fieldOf(s.fields, 'mustFace').value).toBe('NE');
    expect(fieldOf(s.fields, 'mustFace').build('')).toEqual([
      { type: 'room.set_target', payload: { roomId: room.id, mustFace: null } },
    ]);
  });

  it('produces no op when the facing is already what you asked for', () => {
    expect(room).toBeDefined();
    if (room === undefined) return;
    const s = inspectorSelection(house, [room.id], { display });
    // The fixture rooms have no facing, so "Any" is already true.
    expect(fieldOf(s.fields, 'mustFace').build('')).toEqual([]);
  });

  it('offers no delete — you delete a room by deleting a wall', () => {
    expect(room).toBeDefined();
    if (room === undefined) return;
    expect(inspectorSelection(house, [room.id], { display }).actions).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Storey
// ---------------------------------------------------------------------------

describe('storey inspector', () => {
  it('edits floor-to-floor height and folds it', () => {
    const s = inspectorSelection(house, [FIXTURE_IDS.groundStorey], { display });
    const height = fieldOf(s.fields, 'height');
    expect(height.value).toBe(3000);
    const next = applyGroup(doc, height.build(3200), 'g8').model;
    expect(next.house.storeys[0]?.heightMm).toBe(3200);
  });
});

// ---------------------------------------------------------------------------
// The invariant that matters across all of them
// ---------------------------------------------------------------------------

describe('every editable field', () => {
  const selections = [
    inspectorSelection(house, [FIXTURE_IDS.wallSouth], { display }),
    inspectorSelection(house, [FIXTURE_IDS.doorMain], { display }),
    inspectorSelection(house, [FIXTURE_IDS.groundStorey], { display }),
  ];

  it('carries undo copy, because every edit gets an undo toast (§15)', () => {
    for (const s of selections) {
      for (const field of s.fields) {
        if (!field.editable) continue;
        expect(field.undoLabel, `${s.kind}.${field.key}`).not.toBe('');
      }
    }
  });

  it('never throws on a value of the wrong type — it returns no ops', () => {
    for (const s of selections) {
      for (const field of s.fields) {
        expect(() => field.build(true)).not.toThrow();
        expect(() => field.build('')).not.toThrow();
        expect(() => field.build(-1)).not.toThrow();
      }
    }
  });
});
