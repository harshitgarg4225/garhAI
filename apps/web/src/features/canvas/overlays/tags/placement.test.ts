/**
 * Spec: label collision resolution.
 *
 * The property that matters is not "labels look nice" — it is "no two labels
 * overlap, ever, and the same plan lays out the same way twice". Both are
 * asserted directly rather than through a golden snapshot, because a snapshot
 * of coordinates tells you a layout changed without telling you whether the
 * change was wrong.
 */

import { describe, expect, it } from 'vitest';

import {
  overflowedLabels,
  placeLabels,
  shouldReplace,
  ZOOM_REPLACE_RATIO,
  type PlaceableLabel,
  type PlacedLabel,
} from './placement';
import { estimateTextWidth, roomAnchorMm, roomTags, tagsToPlaceable } from './tags';
import { makeTwoRoomPlan } from '@garh/model';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function label(
  id: string,
  x: number,
  y: number,
  overrides: Partial<PlaceableLabel> = {},
): PlaceableLabel {
  return {
    id,
    anchorMm: { x, y },
    halfWidthMm: 500,
    halfHeightMm: 150,
    priority: 1,
    ...overrides,
  };
}

/** Do two placed labels' boxes intersect? Padding is excluded on purpose: the
 *  contract is "no overlap", and padding is comfort on top of it. */
function intersects(a: PlacedLabel, b: PlacedLabel): boolean {
  return (
    Math.abs(a.atMm.x - b.atMm.x) < a.halfWidthMm + b.halfWidthMm &&
    Math.abs(a.atMm.y - b.atMm.y) < a.halfHeightMm + b.halfHeightMm
  );
}

/**
 * THE CONTRACT: no two labels the placer reports as placed may overlap.
 * `overflow` labels are excluded because the placer already told the caller it
 * could not fit them — an excluded-and-declared failure is not the same bug as
 * a silent one.
 */
function assertNoOverlaps(placed: readonly PlacedLabel[]): void {
  const clean = placed.filter((p) => p.kind !== 'overflow');
  for (let i = 0; i < clean.length; i++) {
    for (let j = i + 1; j < clean.length; j++) {
      const a = clean[i];
      const b = clean[j];
      if (a === undefined || b === undefined) continue;
      expect(intersects(a, b), `${a.id} overlaps ${b.id}`).toBe(false);
    }
  }
}

// ---------------------------------------------------------------------------
// Placement
// ---------------------------------------------------------------------------

describe('placeLabels', () => {
  it('leaves a label at its anchor when nothing is in the way', () => {
    const placed = placeLabels([label('a', 0, 0), label('b', 10_000, 0)]);
    expect(placed.map((p) => p.kind)).toEqual(['anchor', 'anchor']);
    expect(placed[0]?.atMm).toEqual({ x: 0, y: 0 });
    expect(placed[0]?.leaderMm).toBeNull();
  });

  it('nudges the second of two labels that want the same spot', () => {
    const placed = placeLabels([label('a', 0, 0), label('b', 100, 0)]);
    assertNoOverlaps(placed);
    // Bigger priority wins the anchor; ties break on id, so 'a' keeps it.
    expect(placed[0]?.kind).toBe('anchor');
    expect(placed[1]?.kind).not.toBe('anchor');
  });

  it('gives the prime spot to the higher priority, whatever the input order', () => {
    const small = label('small', 0, 0, { priority: 1 });
    const big = label('big', 120, 0, { priority: 99 });
    const forwards = placeLabels([small, big]);
    const backwards = placeLabels([big, small]);

    const bigForwards = forwards.find((p) => p.id === 'big');
    const bigBackwards = backwards.find((p) => p.id === 'big');
    expect(bigForwards?.kind).toBe('anchor');
    expect(bigBackwards?.kind).toBe('anchor');
    // …and the layout itself is identical, not merely equivalent.
    expect(bigForwards?.atMm).toEqual(bigBackwards?.atMm);
    expect(forwards.find((p) => p.id === 'small')?.atMm).toEqual(
      backwards.find((p) => p.id === 'small')?.atMm,
    );
  });

  it('returns results in input order even though it places in priority order', () => {
    const placed = placeLabels([
      label('z', 0, 0, { priority: 1 }),
      label('a', 5000, 0, { priority: 9 }),
    ]);
    expect(placed.map((p) => p.id)).toEqual(['z', 'a']);
  });

  it('resolves a dozen labels that all want overlapping spots', () => {
    // A 4x3 grid on a 600x200 pitch: every anchor collides with its neighbours
    // (the padded label box is 1004 x 304), and there is open space around them.
    const labels = Array.from({ length: 12 }, (_, i) =>
      label(`l${String(i).padStart(2, '0')}`, (i % 4) * 600, Math.floor(i / 4) * 200, {
        priority: 12 - i,
      }),
    );
    const placed = placeLabels(labels, { maxNudgeSteps: 8 });
    expect(placed).toHaveLength(12);
    expect(overflowedLabels(placed)).toEqual([]);
    assertNoOverlaps(placed);
  });

  it('reports overflow rather than silently overlapping, when nothing fits', () => {
    // Six labels on one point with a single nudge ring: nine candidate slots,
    // most of which collide with each other. Some must overflow — and the
    // placer must SAY so.
    const labels = Array.from({ length: 6 }, (_, i) =>
      label(`s${String(i)}`, 0, 0, { priority: 6 - i }),
    );
    const placed = placeLabels(labels, { maxNudgeSteps: 1 });
    expect(placed).toHaveLength(6);
    assertNoOverlaps(placed);
    expect(overflowedLabels(placed).length).toBeGreaterThan(0);
  });

  it('prefers a nudge inside the room over a leader line outside it', () => {
    // A wide room: the label can move sideways and stay in.
    const room = [
      { x: 0, y: 0 },
      { x: 8000, y: 0 },
      { x: 8000, y: 3000 },
      { x: 0, y: 3000 },
    ];
    const blocker = label('blocker', 4000, 1500, { priority: 99 });
    const inside = label('inside', 4000, 1500, { priority: 1, boundaryMm: room });
    const placed = placeLabels([blocker, inside]);
    const moved = placed.find((p) => p.id === 'inside');
    expect(moved?.kind).toBe('nudged');
    expect(moved?.leaderMm).toBeNull();
  });

  it('falls back to a leader line when the label cannot stay inside', () => {
    // A room barely bigger than the label itself: every nudge leaves it.
    const tiny = [
      { x: 0, y: 0 },
      { x: 1100, y: 0 },
      { x: 1100, y: 320 },
      { x: 0, y: 320 },
    ];
    const blocker = label('blocker', 550, 160, { priority: 99 });
    const cramped = label('cramped', 550, 160, { priority: 1, boundaryMm: tiny });
    const placed = placeLabels([blocker, cramped]);
    const moved = placed.find((p) => p.id === 'cramped');
    expect(moved?.kind).toBe('leader');
    expect(moved?.leaderMm).not.toBeNull();
  });

  it('draws the leader from the label edge back to the anchor', () => {
    const tiny = [
      { x: 0, y: 0 },
      { x: 1100, y: 0 },
      { x: 1100, y: 320 },
      { x: 0, y: 320 },
    ];
    const placed = placeLabels([
      label('blocker', 550, 160, { priority: 99 }),
      label('cramped', 550, 160, { priority: 1, boundaryMm: tiny }),
    ]);
    const moved = placed.find((p) => p.id === 'cramped');
    const leader = moved?.leaderMm;
    expect(leader).toBeDefined();
    if (leader === undefined || leader === null || moved === undefined) return;
    // Ends at the anchor…
    expect(leader[1]).toEqual({ x: 550, y: 160 });
    // …and starts on the label's own box, not at its centre.
    const dx = Math.abs(leader[0].x - moved.atMm.x);
    const dy = Math.abs(leader[0].y - moved.atMm.y);
    expect(dx <= moved.halfWidthMm + 1e-6 && dy <= moved.halfHeightMm + 1e-6).toBe(true);
  });

  it('drops nothing by default, and drops only on request', () => {
    const labels = Array.from({ length: 6 }, (_, i) =>
      label(`l${String(i)}`, 0, 0, { priority: 6 - i }),
    );
    expect(placeLabels(labels, { maxNudgeSteps: 1 })).toHaveLength(6);
    // The sheet engine's stricter contract: never emit a colliding label.
    const dropped = placeLabels(labels, { maxNudgeSteps: 1, dropUnplaceable: true });
    expect(dropped.length).toBeLessThan(6);
    expect(overflowedLabels(dropped)).toEqual([]);
    assertNoOverlaps(dropped);
  });

  it('handles the empty case without allocating a grid', () => {
    expect(placeLabels([])).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Zoom banding — the §14 compromise
// ---------------------------------------------------------------------------

describe('shouldReplace', () => {
  it('ignores a small zoom change', () => {
    expect(shouldReplace(4, 4 * 1.1)).toBe(false);
    expect(shouldReplace(4, 4 / 1.1)).toBe(false);
  });

  it('re-places once the zoom crosses a band, in either direction', () => {
    expect(shouldReplace(4, 4 * (ZOOM_REPLACE_RATIO + 0.01))).toBe(true);
    expect(shouldReplace(4, 4 / (ZOOM_REPLACE_RATIO + 0.01))).toBe(true);
  });

  it('always places the first time', () => {
    expect(shouldReplace(0, 4)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tag view models
// ---------------------------------------------------------------------------

describe('roomTags', () => {
  const doc = makeTwoRoomPlan();
  const storeyId = doc.house.storeys[0]?.id ?? '';

  it('renders the rooms the model detected — it does not re-detect them', () => {
    const tags = roomTags(doc.house.rooms, storeyId, 'ft-in');
    expect(tags).toHaveLength(2);
    expect(new Set(tags.map((t) => t.roomId))).toEqual(new Set(doc.house.rooms.map((r) => r.id)));
    // 10 661 560 mm² = 114.76 sq ft
    expect(tags[0]?.areaText).toBe('114.8 sq ft');
  });

  it('formats the area per project units', () => {
    const metric = roomTags(doc.house.rooms, storeyId, 'm');
    expect(metric[0]?.areaText).toBe('10.7 m²');
  });

  it('numbers unnamed rooms rather than showing two identical labels', () => {
    const tags = roomTags(doc.house.rooms, storeyId, 'ft-in');
    expect(tags.map((t) => t.nameText).sort()).toEqual(['Room 1', 'Room 2']);
  });

  it('hides rooms on other storeys and rooms that are not rooms', () => {
    expect(roomTags(doc.house.rooms, 'storey_01J000000000000000000XXX', 'ft-in')).toEqual([]);
    const ducts = doc.house.rooms.map((r) => ({ ...r, type: 'duct' as const }));
    expect(roomTags(ducts, storeyId, 'ft-in')).toEqual([]);
  });

  it('anchors inside the room even when the centroid is not', () => {
    // An L: the centroid of this ring falls in the missing quadrant.
    const l = [
      { x: 0, y: 0 },
      { x: 6000, y: 0 },
      { x: 6000, y: 2000 },
      { x: 2000, y: 2000 },
      { x: 2000, y: 6000 },
      { x: 0, y: 6000 },
    ];
    const anchor = roomAnchorMm(l);
    // Inside the L, by the odd-crossing test the model itself uses.
    const inside = (p: { x: number; y: number }): boolean =>
      (p.x >= 0 && p.x <= 2000 && p.y >= 0 && p.y <= 6000) ||
      (p.x >= 0 && p.x <= 6000 && p.y >= 0 && p.y <= 2000);
    expect(inside(anchor)).toBe(true);
  });
});

describe('tagsToPlaceable', () => {
  const doc = makeTwoRoomPlan();
  const storeyId = doc.house.storeys[0]?.id ?? '';
  const tags = roomTags(doc.house.rooms, storeyId, 'ft-in');

  it('scales the label footprint with the zoom, and nothing else does', () => {
    const near = tagsToPlaceable(tags, 1);
    const far = tagsToPlaceable(tags, 10);
    expect(far[0]?.halfWidthMm).toBeCloseTo((near[0]?.halfWidthMm ?? 0) * 10, 6);
    // The anchor is a model coordinate and must NOT move with the zoom.
    expect(far[0]?.anchorMm).toEqual(near[0]?.anchorMm);
  });

  it('orders by area so the big room wins the centre', () => {
    const placeable = tagsToPlaceable(tags, 4);
    expect(placeable[0]?.priority).toBeGreaterThanOrEqual(placeable[1]?.priority ?? 0);
  });

  it('over-estimates text width rather than under-estimating it', () => {
    // Under-estimating lets two labels overlap, which is visible; over-
    // estimating spreads them slightly, which is not.
    expect(estimateTextWidth('MMMM', 10)).toBeGreaterThan(4 * 10 * 0.5);
  });

  it('produces a layout with no overlaps on the demo plan', () => {
    const placed = placeLabels(tagsToPlaceable(tags, 8));
    assertNoOverlaps(placed);
  });
});
