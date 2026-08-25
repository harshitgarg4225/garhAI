/**
 * plain.ts + docPlan.ts — the pure halves of the copilot feature.
 *
 * These are the Phase-6 spec's "op-list → plain-language rendering" tests:
 * a route response's ops must come out as readable rows with the right icon
 * kind and the right highlight targets, with or without the server's own
 * sentences. Fixture ops mirror `services/llm/fixtures/copilot-commands.json`
 * retargeted at the shared `FIXTURE_IDS` plan from `@garh/model`.
 */

import { describe, expect, it } from 'vitest';

import { DEFAULTS, FIXTURE_IDS, makeTwoRoomPlanWithOpenings } from '@garh/model';

import { docPlanForStorey, docPlanViewBox, pickDiffStoreyId } from './docPlan';
import { clarificationChips, describeOp, opElementIds, opKind, toDiffOps } from './plain';
import type { CopilotWireOp } from './types';

const doc = makeTwoRoomPlanWithOpenings();

const resizeDoor: CopilotWireOp = {
  type: 'opening.resize',
  payload: { openingId: FIXTURE_IDS.doorMain, widthMm: 900 },
};

const moveWall: CopilotWireOp = {
  type: 'wall.move',
  payload: { wallId: FIXTURE_IDS.wallSpine, a: { x: 3300, y: 0 }, b: { x: 3300, y: 4000 } },
};

const addWindow: CopilotWireOp = {
  type: 'opening.add',
  payload: {
    id: 'opening_01J000000000000000000000W2',
    wallId: FIXTURE_IDS.wallNorth,
    kind: 'window',
    widthMm: 1200,
    heightMm: 1200,
    sillMm: 900,
    offsetMm: 1500,
    swing: 'in-left',
  },
};

const deleteWall: CopilotWireOp = { type: 'wall.delete', payload: { wallId: FIXTURE_IDS.wallSpine } };

const assignRoom: CopilotWireOp = {
  type: 'room.assign',
  payload: { roomId: 'room_01J0000000000000000000000R1', type: 'guest_bedroom' },
};

describe('opKind', () => {
  it('maps taxonomy verbs onto the diff row kinds', () => {
    expect(opKind('opening.add')).toBe('add');
    expect(opKind('wall.move')).toBe('move');
    expect(opKind('opening.resize')).toBe('resize');
    expect(opKind('wall.set_thickness')).toBe('resize');
    expect(opKind('wall.delete')).toBe('remove');
    expect(opKind('room.assign')).toBe('assign');
    expect(opKind('brief.update')).toBe('edit');
  });

  it('reads the real verb out of composite `.set` ops', () => {
    expect(opKind('furniture.set', { action: 'place' })).toBe('add');
    expect(opKind('column.set', { action: 'delete' })).toBe('remove');
    expect(opKind('balcony.set', { action: 'edit' })).toBe('edit');
  });
});

describe('opElementIds', () => {
  it('collects payload.id and every *Id field, deduped', () => {
    expect(opElementIds(resizeDoor)).toEqual([FIXTURE_IDS.doorMain]);
    expect(opElementIds(addWindow)).toEqual([
      'opening_01J000000000000000000000W2',
      FIXTURE_IDS.wallNorth,
    ]);
    expect(opElementIds(moveWall)).toEqual([FIXTURE_IDS.wallSpine]);
  });

  it('never treats grouping metadata as an element', () => {
    const op: CopilotWireOp = {
      type: 'wall.delete',
      payload: { wallId: 'wall_x', clientOpId: 'cop_1', groupId: 'grp_1' },
    };
    expect(opElementIds(op)).toEqual(['wall_x']);
  });
});

describe('describeOp (the fallback sentences)', () => {
  it('writes plain words, never the op type or an element id', () => {
    const ops = [resizeDoor, moveWall, addWindow, deleteWall, assignRoom];
    for (const op of ops) {
      const text = describeOp(op, doc);
      expect(text).not.toContain('.');
      expect(text).not.toMatch(/_01[A-Z0-9]/i);
      expect(text.length).toBeGreaterThan(5);
    }
  });

  it('names the opening kind by looking it up in the document', () => {
    // doorMain IS a door in the fixture plan — the sentence must say so.
    expect(describeOp(resizeDoor, doc).toLowerCase()).toContain('door');
    // Without a document it stays honest and generic.
    expect(describeOp(resizeDoor, null).toLowerCase()).toContain('opening');
  });

  it('formats lengths per the project units (golden rule 6)', () => {
    // The fixture doc displays ft-in; 900 mm ≈ 2'-11".
    const text = describeOp(resizeDoor, doc);
    expect(text).toMatch(/2'|911|900/); // ft-in string, never raw unlabelled number soup
    // Raw mm when no document is at hand.
    expect(describeOp(resizeDoor, null)).toContain('900 mm');
  });
});

describe('toDiffOps', () => {
  it('prefers the server sentence and index-aligns it with the ops', () => {
    const rows = toDiffOps(
      [resizeDoor, moveWall],
      ['Widen the kitchen door to 900mm.'],
      doc,
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]?.text).toBe('Widen the kitchen door to 900mm.');
    // Second op has no server line: the fallback fills in.
    expect(rows[1]?.text.toLowerCase()).toContain('wall');
    expect(rows[0]?.opType).toBe('opening.resize');
    expect(rows[0]?.kind).toBe('resize');
    expect(rows[1]?.kind).toBe('move');
    expect(rows[0]?.elementIds).toEqual([FIXTURE_IDS.doorMain]);
    // Stable, unique React keys.
    expect(new Set(rows.map((r) => r.id)).size).toBe(2);
  });

  it('falls back when the server sentence is empty', () => {
    const rows = toDiffOps([deleteWall], [''], doc);
    expect(rows[0]?.text.toLowerCase()).toContain('remove');
  });
});

describe('clarificationChips', () => {
  it('mines A-or-B alternatives from the shipped fixture question', () => {
    const chips = clarificationChips(
      'Which bedroom should I enlarge, and should I take the space from the passage or the adjoining room?',
    );
    expect(chips).toContain('the passage');
    expect(chips).toContain('the adjoining room');
  });

  it('returns nothing rather than guessing on an open question', () => {
    expect(clarificationChips('Which bedroom should I enlarge?')).toEqual([]);
  });

  it('caps the chip count', () => {
    const chips = clarificationChips(
      'Should it be the kitchen or the pantry or the store or the lobby or the porch?',
    );
    expect(chips.length).toBeLessThanOrEqual(4);
  });
});

describe('docPlan geometry', () => {
  it('extracts one storey: walls, opening cuts, room labels', () => {
    const g = docPlanForStorey(doc, FIXTURE_IDS.groundStorey);
    expect(g.walls).toHaveLength(5);
    expect(g.openings).toHaveLength(2); // main door + west window
    expect(g.labels.length).toBeGreaterThanOrEqual(2); // two detected rooms

    const door = g.openings.find((o) => o.id === FIXTURE_IDS.doorMain);
    expect(door).toBeDefined();
    // The south wall runs (0,0)→(6000,0); the door is centred at offset 1500,
    // width DEFAULTS.doorWidthMm — its cut must straddle x=1500 on y=0.
    const half = Math.trunc(DEFAULTS.doorWidthMm / 2);
    expect(door?.a).toEqual({ x: 1500 - half, y: 0 });
    expect(door?.b).toEqual({ x: 1500 + half, y: 0 });
  });

  it('returns empty lists for a storey that does not exist', () => {
    const g = docPlanForStorey(doc, 'storey_nope');
    expect(g.walls).toEqual([]);
    expect(docPlanViewBox([g])).toBeNull();
  });

  it('frames both documents identically (before/after share a viewBox)', () => {
    const g = docPlanForStorey(doc, FIXTURE_IDS.groundStorey);
    const view = docPlanViewBox([g, g]);
    expect(view).not.toBeNull();
    // Y is flipped exactly once: a point at y=4000 lands at -4000.
    expect(view?.toView({ x: 0, y: 4000 })).toEqual({ x: 0, y: -4000 });
  });

  it('picks the storey a touched element lives on, else the active one', () => {
    expect(pickDiffStoreyId(doc, [FIXTURE_IDS.wallSpine], null)).toBe(FIXTURE_IDS.groundStorey);
    expect(pickDiffStoreyId(doc, [FIXTURE_IDS.doorMain], null)).toBe(FIXTURE_IDS.groundStorey);
    expect(pickDiffStoreyId(doc, [], FIXTURE_IDS.groundStorey)).toBe(FIXTURE_IDS.groundStorey);
    // Unknown ids + no active storey → first storey, never a crash.
    expect(pickDiffStoreyId(doc, ['wall_unknown'], null)).toBe(doc.house.storeys[0]?.id);
  });
});
