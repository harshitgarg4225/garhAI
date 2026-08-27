/**
 * THE PHASE-5 CONTRACT SPEC: scrubbing the sun does not dirty geometry.
 *
 * §14 gives a model edit a <100 ms dirty-storey rebuild budget — which only
 * holds if things that are NOT model edits never trigger one. The sun
 * scrubber is the highest-frequency such control (a drag emits dozens of
 * changes a second), so this spec pins the whole isolation chain:
 *
 *  1. sun state lives outside the ProjectDoc — a full-day scrub leaves
 *     `stateHash(doc)` byte-identical (the same hash the server compares,
 *     so this is the strongest "nothing changed" statement the repo has);
 *  2. no op is dispatched and the model store never publishes — the mesh
 *     caches key off the document, so no publish ⇒ no rebuild;
 *  3. the building extent (shadow-camera framing) is a pure function of the
 *     house document, so `SunLight`'s identity-keyed cache can never
 *     recompute it mid-scrub;
 *  4. and the scrub is not a no-op: the computed light frame really moves.
 */

import { describe, expect, it } from 'vitest';

import { makeTwoRoomPlan, stateHash } from '@garh/model';

import { useModelStore } from '../../../stores/model';
import { buildingExtentOf } from './buildingBbox';
import { computeSunFrame } from './frame';
import { initialSunFields, useSunStore } from './sunStore';

const BLR = { latDeg: 12.9716, lonDeg: 77.5946 };

describe('sun scrub — geometry invariance', () => {
  it('a full-day scrub leaves the document hash, the op queue and the store untouched', () => {
    const doc = makeTwoRoomPlan();
    const hashBefore = stateHash(doc);

    // Watch the ONLY writer of design state. Any publish here means some part
    // of the scrub path reached the model store — the exact bug this pins.
    let modelStorePublished = 0;
    const unsubscribe = useModelStore.subscribe(() => {
      modelStorePublished += 1;
    });
    const pendingBefore = useModelStore.getState().pending.length;
    const undoBefore = useModelStore.getState().undoStack.length;

    // Drive the store exactly as the panel does: 288 five-minute steps.
    const sun = useSunStore.getState();
    sun.setDay({ year: 2026, month: 6, day: 21 });
    const frames: number[] = [];
    for (let minutes = 0; minutes < 1440; minutes += 5) {
      useSunStore.getState().setMinutesOfDay(minutes);
      const state = useSunStore.getState();
      const frame = computeSunFrame(state.day, state.minutesOfDay, BLR.latDeg, BLR.lonDeg, 0);
      frames.push(frame.sunIntensity);
    }
    unsubscribe();

    // 1. + 2. — nothing about the document or its pipeline moved.
    expect(stateHash(doc)).toBe(hashBefore);
    expect(modelStorePublished).toBe(0);
    expect(useModelStore.getState().pending.length).toBe(pendingBefore);
    expect(useModelStore.getState().undoStack.length).toBe(undoBefore);

    // 4. — and the scrub was not a dead control: the light really changed.
    const distinct = new Set(frames.map((v) => v.toFixed(4)));
    expect(distinct.size).toBeGreaterThan(10);
    expect(Math.min(...frames)).toBe(0); // night
    expect(Math.max(...frames)).toBeGreaterThan(2); // full noon sun
  });

  it('building extent is pure in the house document — the cache key holds', () => {
    const doc = makeTwoRoomPlan();
    const a = buildingExtentOf(doc.house);
    const b = buildingExtentOf(doc.house);
    // Pure: same input, equal output (identity caching by `SunLight` is
    // therefore only an optimisation, never a behaviour change).
    expect(a).toEqual(b);
    expect(a).not.toBeNull();
    if (a === null) return;
    // The two-room fixture is a real plan: a sane, non-degenerate box.
    expect(a.box.maxX).toBeGreaterThan(a.box.minX);
    expect(a.box.maxY).toBeGreaterThan(a.box.minY);
    expect(a.heightMm).toBeGreaterThan(0);
  });

  it('an empty model has no extent — fit and shadows must teach, not invent', () => {
    const empty = makeTwoRoomPlan();
    expect(
      buildingExtentOf({
        ...empty.house,
        walls: [],
        slabs: [],
        rooms: [],
        balconies: [],
        columns: [],
        stairs: [],
      }),
    ).toBeNull();
  });

  it('initialSunFields is deterministic for a pinned clock', () => {
    // 2026-06-21 07:00 UTC = 12:30 IST.
    const fields = initialSunFields(Date.UTC(2026, 5, 21, 7, 0, 0));
    expect(fields.day).toEqual({ year: 2026, month: 6, day: 21 });
    expect(fields.minutesOfDay).toBe(750);
  });
});
