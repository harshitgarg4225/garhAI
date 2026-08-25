/**
 * The F2 pure logic: completeness computation and merge-patch construction.
 *
 * These two functions carry numbers that leave this feature — completeness is
 * stamped on every `brief.update` op and shown on the dashboard chip, and the
 * merge patch is what actually folds into the document — so both are pinned
 * here rather than trusted to component behaviour.
 */

import { describe, expect, it } from 'vitest';

import { emptyBrief, fold, emptyProjectDoc, fromSqft, type JsonObject } from '@garh/model';

import {
  COMPLETENESS_CHECKLIST,
  COMPLETENESS_TOTAL_WEIGHT,
  computeCompleteness,
} from './completeness';
import {
  applyMergePatch,
  briefUpdateOp,
  canonicaliseParsedData,
  diffMergePatch,
  pruneUnchanged,
  setBriefField,
} from './mergePatch';
import {
  addBedroom,
  areaTargetMm2,
  bandForBudget,
  bedroomRows,
  normaliseRooms,
  parseRupees,
  removeBedroom,
  roomCount,
  setRoomCount,
  updateBedroom,
  withLivingDining,
  type RoomRequest,
} from './types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A brief with every checklist item answered. */
function completeBrief(): JsonObject {
  return {
    familySize: 4,
    storeys: 2,
    terraceAccess: true,
    futureExpansion: false,
    parkingCount: 1,
    kitchenType: 'closed',
    livingDining: 'combined',
    budgetInr: 6_000_000,
    styleKitId: 'contemporary',
    vastuDecided: true,
    rooms: [
      { type: 'bedroom_master', count: 1, bath: 'attached' },
      { type: 'bedroom', count: 2, bath: 'common' },
      { type: 'kitchen', count: 1 },
      { type: 'living_dining', count: 1 },
      { type: 'bath_wc', count: 2 },
    ],
  };
}

// ---------------------------------------------------------------------------
// Completeness
// ---------------------------------------------------------------------------

describe('computeCompleteness', () => {
  it('checklist weights sum to exactly 100', () => {
    expect(COMPLETENESS_TOTAL_WEIGHT).toBe(100);
    // Integers only — the score is stored in an integer DB column via the op.
    for (const item of COMPLETENESS_CHECKLIST) {
      expect(Number.isSafeInteger(item.weight)).toBe(true);
      expect(item.weight).toBeGreaterThan(0);
    }
  });

  it('an empty brief scores 0 and lists every item, heaviest first', () => {
    const result = computeCompleteness({});
    expect(result.score).toBe(0);
    expect(result.missing).toHaveLength(COMPLETENESS_CHECKLIST.length);
    expect(result.missing[0]?.id).toBe('bedrooms'); // weight 20 leads
    const weights = result.missing.map((m) => m.weight);
    expect([...weights].sort((a, b) => b - a)).toEqual(weights);
  });

  it('a fully answered brief scores exactly 100 with nothing missing', () => {
    const result = computeCompleteness(completeBrief());
    expect(result.score).toBe(100);
    expect(result.missing).toEqual([]);
    expect(result.answered).toHaveLength(COMPLETENESS_CHECKLIST.length);
  });

  it('answers add exactly their weight', () => {
    const empty = computeCompleteness({}).score;
    const withBeds = computeCompleteness({
      rooms: [{ type: 'bedroom', count: 2 }],
    }).score;
    expect(withBeds - empty).toBe(20);

    const withBudget = computeCompleteness({ budgetInr: 5_000_000 }).score;
    expect(withBudget - empty).toBe(10);
  });

  it('bath question is answered by bath rooms OR per-bedroom bath choices', () => {
    const viaRooms = computeCompleteness({ rooms: [{ type: 'bath_wc', count: 1 }] });
    expect(viaRooms.answered).toContain('baths');

    const viaBedroom = computeCompleteness({
      rooms: [{ type: 'bedroom_master', count: 1, bath: 'attached' }],
    });
    expect(viaBedroom.answered).toContain('baths');
  });

  it('vastu counts only when explicitly decided — "off by default" is not an answer', () => {
    expect(computeCompleteness({}).answered).not.toContain('vastu');
    expect(computeCompleteness({ vastuDecided: true }).answered).toContain('vastu');
  });

  it('tri-state toggles count once DEFINED, either way', () => {
    expect(computeCompleteness({ terraceAccess: false }).answered).toContain('terrace');
    expect(computeCompleteness({}).answered).not.toContain('terrace');
  });

  it('never crashes on garbage data — unreadable fields are just unanswered', () => {
    const result = computeCompleteness({
      rooms: 'not-an-array',
      storeys: 1.5,
      budgetInr: 'sixty lakh',
      vastuDecided: 'yes',
    });
    expect(result.score).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// RFC 7386 merge patch
// ---------------------------------------------------------------------------

describe('applyMergePatch (RFC 7386)', () => {
  it('merges nested objects key by key', () => {
    expect(applyMergePatch({ a: { x: 1, y: 2 }, b: 1 }, { a: { y: 3 } })).toEqual({
      a: { x: 1, y: 3 },
      b: 1,
    });
  });

  it('null deletes a key', () => {
    expect(applyMergePatch({ budgetInr: 5, storeys: 2 }, { budgetInr: null })).toEqual({
      storeys: 2,
    });
  });

  it('arrays replace wholesale — the rooms-list contract', () => {
    const before = { rooms: [{ type: 'bedroom', count: 3 }] };
    const after = applyMergePatch(before, { rooms: [{ type: 'kitchen', count: 1 }] });
    expect(after).toEqual({ rooms: [{ type: 'kitchen', count: 1 }] });
  });

  it('a non-object value inside the patch replaces whatever was there', () => {
    expect(applyMergePatch({ a: { deep: true } }, { a: 'text' })).toEqual({ a: 'text' });
    expect(applyMergePatch({ a: 'text' }, { a: { deep: true } })).toEqual({ a: { deep: true } });
  });

  it('does not mutate its inputs', () => {
    const target = { a: { x: 1 }, keep: [1, 2] };
    const patch = { a: { x: 2 } };
    const snapshot = JSON.parse(JSON.stringify(target)) as JsonObject;
    applyMergePatch(target, patch);
    expect(target).toEqual(snapshot);
  });

  it('agrees with the model core fold for brief.update', () => {
    // The client predicts the merged brief to stamp completeness; the fold is
    // the authority. The two must agree or the stamp is a lie.
    const doc = emptyProjectDoc();
    const patch = { storeys: 2, rooms: [{ type: 'bedroom', count: 2 }] };
    const folded = fold(doc, { type: 'brief.update', payload: { patch } }).model;
    expect(applyMergePatch(doc.brief.data, patch)).toEqual(folded.brief.data);
  });
});

describe('diffMergePatch / pruneUnchanged', () => {
  it('diff produces the minimal patch, with null for removed keys', () => {
    const from = { a: 1, b: { x: 1, y: 2 }, gone: true };
    const to = { a: 1, b: { x: 1, y: 3 } };
    expect(diffMergePatch(from, to)).toEqual({ b: { y: 3 }, gone: null });
  });

  it('diff round-trips: apply(from, diff(from,to)) === to', () => {
    const from = { a: 1, b: { x: 1 }, arr: [1, 2] };
    const to = { a: 2, b: { x: 1, z: 9 }, arr: [3], extra: 'hi' };
    expect(applyMergePatch(from, diffMergePatch(from, to))).toEqual(to);
  });

  it('pruneUnchanged drops keys the brief already holds, never emits deletions', () => {
    const current = { storeys: 2, budgetInr: 5_000_000, kitchenType: 'open' };
    const parse = { storeys: 2, budgetInr: 6_000_000 };
    expect(pruneUnchanged(parse, current)).toEqual({ budgetInr: 6_000_000 });
    // A key present locally but absent from the parse is left alone.
    expect(pruneUnchanged(parse, current)).not.toHaveProperty('kitchenType');
  });
});

describe('briefUpdateOp', () => {
  it('builds a brief.update op with completeness computed on the MERGED data', () => {
    const brief = { ...emptyBrief(), data: { budgetInr: 5_000_000 } };
    const patch = { rooms: [{ type: 'bedroom', count: 1 }] };
    const op = briefUpdateOp(brief, patch);

    expect(op.type).toBe('brief.update');
    expect(op.payload.patch).toBe(patch);
    // budget (10, already present) + bedrooms (20, from the patch) = 30.
    expect(op.payload.completeness).toBe(30);
    expect(op.payload.vastuMode).toBeUndefined();
  });

  it('carries vastuMode only when the mode itself changes', () => {
    const op = briefUpdateOp(emptyBrief(), { vastuDecided: true }, { vastuMode: 'advisory' });
    expect(op.payload.vastuMode).toBe('advisory');
    // vastuDecided (5) — computed after the merge.
    expect(op.payload.completeness).toBe(5);
  });

  it('a deleting patch lowers the stamped completeness', () => {
    const brief = { ...emptyBrief(), data: completeBrief() };
    const op = briefUpdateOp(brief, { budgetInr: null });
    expect(op.payload.completeness).toBe(90);
  });
});

// ---------------------------------------------------------------------------
// Assumption-chip edits (dotted paths) and parse canonicalisation
// ---------------------------------------------------------------------------

describe('setBriefField', () => {
  it('sets a top-level scalar from a brief.-prefixed path', () => {
    expect(setBriefField({ storeys: 2 }, 'brief.storeys', 3)).toEqual({ storeys: 3 });
    expect(setBriefField({}, 'brief.parkingCount', 1)).toEqual({ parkingCount: 1 });
  });

  it('null deletes the scalar', () => {
    expect(setBriefField({ budgetInr: 1 }, 'brief.budgetInr', null)).toEqual({});
  });

  it('edits a room count through brief.rooms.<type>.count', () => {
    const data: JsonObject = { rooms: [{ type: 'bath_wc', count: 2 }], storeys: 2 };
    const next = setBriefField(data, 'brief.rooms.bath_wc.count', 3);
    expect(next).not.toBeNull();
    expect(roomCount((next as unknown as { rooms: RoomRequest[] }).rooms, 'bath_wc')).toBe(3);
    // Untouched fields survive.
    expect((next as JsonObject)['storeys']).toBe(2);
  });

  it('refuses what it cannot edit instead of guessing', () => {
    expect(setBriefField({}, 'brief.rooms.bath_wc.count', 'two')).toBeNull();
    expect(setBriefField({}, 'brief.rooms.bath_wc.notes', 'x')).toBeNull();
    expect(setBriefField({}, '', 1)).toBeNull();
  });

  it('does not mutate the input object', () => {
    const data: JsonObject = { rooms: [{ type: 'wc', count: 1 }] };
    const snapshot = JSON.parse(JSON.stringify(data)) as JsonObject;
    setBriefField(data, 'brief.rooms.wc.count', 2);
    expect(data).toEqual(snapshot);
  });
});

describe('canonicaliseParsedData', () => {
  it('normalises grouped bedrooms into one entry per room', () => {
    const out = canonicaliseParsedData({
      storeys: 2,
      rooms: [
        { type: 'bedroom', count: 2 },
        { type: 'bedroom_master', count: 1 },
      ],
    });
    const rooms = out['rooms'] as unknown as RoomRequest[];
    expect(rooms).toHaveLength(3);
    expect(rooms[0]?.type).toBe('bedroom_master');
    expect(rooms.every((r) => r.count === 1)).toBe(true);
  });

  it('passes data without rooms through untouched', () => {
    const data = { storeys: 2 };
    expect(canonicaliseParsedData(data)).toBe(data);
  });
});

// ---------------------------------------------------------------------------
// Room-list helpers (the patches the form constructs)
// ---------------------------------------------------------------------------

describe('rooms helpers', () => {
  it('normaliseRooms: bedrooms explode, others group, zero-counts drop', () => {
    const rooms = normaliseRooms([
      { type: 'balcony', count: 1 },
      { type: 'bedroom', count: 2 },
      { type: 'balcony', count: 1 },
      { type: 'store', count: 0 },
    ]);
    expect(rooms.map((r) => [r.type, r.count])).toEqual([
      ['bedroom_master', 1],
      ['bedroom', 1],
      ['balcony', 2],
    ]);
  });

  it('the first bedroom is always the master, even after removal', () => {
    let rooms = addBedroom(addBedroom(undefined));
    expect(bedroomRows(rooms)[0]?.type).toBe('bedroom_master');
    rooms = removeBedroom(rooms, 0);
    expect(bedroomRows(rooms)).toHaveLength(1);
    expect(bedroomRows(rooms)[0]?.type).toBe('bedroom_master');
  });

  it('updateBedroom patches one row and keeps the rest intact', () => {
    const rooms = addBedroom(addBedroom([{ type: 'kitchen', count: 1 }]));
    const next = updateBedroom(rooms, 1, { bath: 'attached', targetAreaMm2: fromSqft(140) });
    const beds = bedroomRows(next);
    expect(beds[1]?.bath).toBe('attached');
    expect(beds[1]?.targetAreaMm2).toBe(fromSqft(140));
    expect(beds[0]?.bath).not.toBe('attached');
    expect(roomCount(next, 'kitchen')).toBe(1);
  });

  it('setRoomCount replaces the grouped entry; count 0 removes it', () => {
    let rooms = setRoomCount(undefined, 'pooja', 1);
    expect(roomCount(rooms, 'pooja')).toBe(1);
    rooms = setRoomCount(rooms, 'pooja', 0);
    expect(roomCount(rooms, 'pooja')).toBe(0);
  });

  it('withLivingDining swaps between combined and separate', () => {
    const combined = withLivingDining([{ type: 'living', count: 1 }, { type: 'dining', count: 1 }], 'combined');
    expect(roomCount(combined, 'living_dining')).toBe(1);
    expect(roomCount(combined, 'living')).toBe(0);

    const separate = withLivingDining(combined, 'separate');
    expect(roomCount(separate, 'living')).toBe(1);
    expect(roomCount(separate, 'dining')).toBe(1);
    expect(roomCount(separate, 'living_dining')).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Budget helpers
// ---------------------------------------------------------------------------

describe('budget helpers', () => {
  it('parseRupees reads Indian money shorthand into whole rupees', () => {
    expect(parseRupees('45,00,000')).toBe(4_500_000);
    expect(parseRupees('₹45L')).toBe(4_500_000);
    expect(parseRupees('60 lakh')).toBe(6_000_000);
    expect(parseRupees('1.2 Cr')).toBe(12_000_000);
    expect(parseRupees('8500000')).toBe(8_500_000);
    expect(parseRupees('500k')).toBe(500_000);
    expect(parseRupees('')).toBeNull();
    expect(parseRupees('sixty lakh')).toBeNull();
  });

  it('areaTargetMm2 derives an integer mm² target from budget ÷ rate', () => {
    // ₹60,00,000 at ₹2,000/sq ft = 3,000 sq ft exactly.
    expect(areaTargetMm2(6_000_000, 2_000)).toBe(fromSqft(3_000));
    expect(areaTargetMm2(undefined, 2_000)).toBeNull();
    expect(areaTargetMm2(6_000_000, 0)).toBeNull();
    const target = areaTargetMm2(5_555_555, 1_850);
    expect(target).not.toBeNull();
    expect(Number.isSafeInteger(target)).toBe(true);
  });

  it('bandForBudget keeps the band consistent with an exact figure', () => {
    expect(bandForBudget(2_000_000)).toBe('under-25l');
    expect(bandForBudget(4_000_000)).toBe('25l-50l');
    expect(bandForBudget(7_500_000)).toBe('50l-1cr');
    expect(bandForBudget(15_000_000)).toBe('1cr-2cr');
    expect(bandForBudget(30_000_000)).toBe('over-2cr');
  });
});
