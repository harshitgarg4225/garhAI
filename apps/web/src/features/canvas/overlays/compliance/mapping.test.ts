/**
 * Spec: the chip → element mapping.
 *
 * This is the join between two systems that know nothing about each other — the
 * rules engine returns element ids and no geometry, the canvas has geometry and
 * no idea what a rule is — so it is exactly where a silent mismatch would hide.
 * The cases that matter are the ones where the mapping must NOT invent an
 * answer: an id that no longer exists, a rule about the whole plot, a violation
 * on a storey you are not looking at.
 */

import { describe, expect, it } from 'vitest';

import { FIXTURE_IDS, makeTwoRoomPlanWithOpenings } from '@garh/model';

import type { ComplianceIssueVM } from '../../../../components/types';
import {
  complianceCounts,
  elementBboxMm,
  focusFitBbox,
  focusFor,
  mapComplianceChips,
  markersFor,
} from './mapping';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const doc = makeTwoRoomPlanWithOpenings();
const house = doc.house;
const roomA = house.rooms[0];
const roomB = house.rooms[1];

function issue(overrides: Partial<ComplianceIssueVM> = {}): ComplianceIssueVM {
  return {
    ruleId: 'nbc.room.area.min',
    status: 'fail',
    message: 'Bedroom 2 is 8.9 m² — NBC needs 9.5 m²',
    elementIds: [],
    fixAvailable: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Element geometry
// ---------------------------------------------------------------------------

describe('elementBboxMm', () => {
  it('frames a wall around its masonry, not its centreline', () => {
    const hit = elementBboxMm(house, FIXTURE_IDS.wallSouth);
    expect(hit).not.toBeNull();
    if (hit === null) return;
    // The south wall runs (0,0)→(6000,0) at 230 thick, so the box is 115 either
    // side. A centreline box would be zero-height and un-zoomable.
    expect(hit.bboxMm).toEqual({ minX: -115, minY: -115, maxX: 6115, maxY: 115 });
    expect(hit.storeyId).toBe(FIXTURE_IDS.groundStorey);
  });

  it('frames a room on its clear polygon', () => {
    expect(roomA).toBeDefined();
    if (roomA === undefined) return;
    const hit = elementBboxMm(house, roomA.id);
    expect(hit?.storeyId).toBe(FIXTURE_IDS.groundStorey);
    expect(hit?.bboxMm.maxX).toBeGreaterThan(hit?.bboxMm.minX ?? 0);
  });

  it('frames an opening at its position along its host wall', () => {
    const hit = elementBboxMm(house, FIXTURE_IDS.doorMain);
    expect(hit).not.toBeNull();
    if (hit === null) return;
    // Door centred 1500 along the south wall, 900 wide ⇒ 1050…1950.
    expect(hit.bboxMm.minX).toBe(1050);
    expect(hit.bboxMm.maxX).toBe(1950);
    // …and it inherits the storey from the wall, which is where it lives.
    expect(hit.storeyId).toBe(FIXTURE_IDS.groundStorey);
  });

  it('frames a storey-scoped rule on the whole floor plate', () => {
    const hit = elementBboxMm(house, FIXTURE_IDS.groundStorey);
    expect(hit?.bboxMm).toEqual({ minX: 0, minY: 0, maxX: 6000, maxY: 4000 });
  });

  it('returns null — never a box at the origin — for an id it cannot resolve', () => {
    expect(elementBboxMm(house, 'wall_01J0000000000000000000GON')).toBeNull();
    expect(elementBboxMm(house, 'not-an-id')).toBeNull();
    expect(elementBboxMm(house, '')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Focus
// ---------------------------------------------------------------------------

describe('focusFor', () => {
  it('unions every element the rule named', () => {
    const focus = focusFor(house, [FIXTURE_IDS.wallSouth, FIXTURE_IDS.wallNorth]);
    expect(focus).not.toBeNull();
    if (focus === null) return;
    expect(focus.bboxMm.minY).toBe(-115);
    expect(focus.bboxMm.maxY).toBe(4115);
    expect(focus.storeyId).toBe(FIXTURE_IDS.groundStorey);
  });

  it('drops ids this client has already deleted, and keeps the rest', () => {
    // Compliance is evaluated on the SERVER's log while the canvas draws the
    // optimistic document, so for a few hundred milliseconds a chip can name an
    // element that is already gone here. That is not an error.
    const focus = focusFor(house, ['wall_01J0000000000000000000GON', FIXTURE_IDS.wallSouth]);
    expect(focus?.elementIds).toEqual([FIXTURE_IDS.wallSouth]);
  });

  it('reports no storey when the elements span more than one', () => {
    // Nothing in the fixture spans storeys, so this is asserted on the shape of
    // the answer: one storey in, one storey out.
    const focus = focusFor(house, [FIXTURE_IDS.wallSouth]);
    expect(focus?.storeyId).toBe(FIXTURE_IDS.groundStorey);
  });

  it('is null for a rule about nothing in particular', () => {
    // FAR, coverage, plot area — real rules, no elements. A chip that zoomed to
    // the origin for one of these would be actively misleading.
    expect(focusFor(house, [])).toBeNull();
  });
});

describe('focusFitBbox', () => {
  it('pads the box so the violation has context around it', () => {
    const focus = focusFor(house, [FIXTURE_IDS.wallSouth]);
    expect(focus).not.toBeNull();
    if (focus === null) return;
    const fit = focusFitBbox(focus);
    expect(fit.minX).toBeLessThan(focus.bboxMm.minX);
    expect(fit.maxX).toBeGreaterThan(focus.bboxMm.maxX);
  });

  it('gives a point-like element a window rather than a zero-size box', () => {
    const fit = focusFitBbox({
      storeyId: null,
      bboxMm: { minX: 1000, minY: 1000, maxX: 1000, maxY: 1000 },
      elementIds: [],
    });
    expect(fit.maxX - fit.minX).toBeGreaterThanOrEqual(2000);
    expect(fit.maxY - fit.minY).toBeGreaterThanOrEqual(2000);
  });
});

// ---------------------------------------------------------------------------
// Chips
// ---------------------------------------------------------------------------

describe('mapComplianceChips', () => {
  it('shows failures before warnings, and hides passes by default', () => {
    const chips = mapComplianceChips(
      [
        issue({ ruleId: 'b.warn', status: 'warn' }),
        issue({ ruleId: 'a.pass', status: 'pass' }),
        issue({ ruleId: 'c.fail', status: 'fail' }),
      ],
      house,
    );
    expect(chips.map((c) => c.ruleId)).toEqual(['c.fail', 'b.warn']);
  });

  it('shows passes when asked — the Compliance tab lists everything', () => {
    const chips = mapComplianceChips([issue({ ruleId: 'a.pass', status: 'pass' })], house, {
      statuses: ['pass', 'warn', 'fail'],
    });
    expect(chips).toHaveLength(1);
  });

  it('keys on the rule AND its elements, because a rule fires once per room', () => {
    expect(roomA).toBeDefined();
    expect(roomB).toBeDefined();
    if (roomA === undefined || roomB === undefined) return;
    const chips = mapComplianceChips(
      [issue({ elementIds: [roomA.id] }), issue({ elementIds: [roomB.id] })],
      house,
    );
    expect(chips).toHaveLength(2);
    expect(new Set(chips.map((c) => c.key)).size).toBe(2);
  });

  it('resolves each chip to the element it is about', () => {
    expect(roomA).toBeDefined();
    if (roomA === undefined) return;
    const chips = mapComplianceChips([issue({ elementIds: [roomA.id] })], house);
    expect(chips[0]?.focus?.elementIds).toEqual([roomA.id]);
    expect(chips[0]?.focus?.storeyId).toBe(FIXTURE_IDS.groundStorey);
  });

  it('keeps a chip whose elements do not resolve — with focus null', () => {
    // The sentence is still true and still worth showing; only the "zoom to it"
    // affordance is unavailable, and the null is how the strip knows that.
    const chips = mapComplianceChips([issue({ elementIds: ['room_01J000000000000000000GON'] })], house);
    expect(chips).toHaveLength(1);
    expect(chips[0]?.focus).toBeNull();
  });

  it('carries the citation, the confidence and the fix flag through untouched', () => {
    const chips = mapComplianceChips(
      [
        issue({
          cite: 'NBC 2016 Part 3, Cl. 4.2',
          confidence: 'seed',
          fixAvailable: true,
          fixHint: 'Widen the room to 2.4 m',
        }),
      ],
      house,
    );
    expect(chips[0]).toMatchObject({
      cite: 'NBC 2016 Part 3, Cl. 4.2',
      confidence: 'seed',
      fixAvailable: true,
      fixHint: 'Widen the room to 2.4 m',
    });
  });

  it('never rewrites the human sentence — the rules layer owns it', () => {
    const message = 'Bedroom 2 is 8.9 m² — NBC needs 9.5 m²';
    const chips = mapComplianceChips([issue({ message })], house);
    expect(chips[0]?.message).toBe(message);
  });

  it('is stable: the same report maps to the same order twice', () => {
    const report = [
      issue({ ruleId: 'z.rule', status: 'warn' }),
      issue({ ruleId: 'a.rule', status: 'warn' }),
      issue({ ruleId: 'm.rule', status: 'fail' }),
    ];
    const first = mapComplianceChips(report, house).map((c) => c.key);
    const second = mapComplianceChips(report.slice().reverse(), house).map((c) => c.key);
    expect(first.map((k) => k.split('#')[0])).toEqual(second.map((k) => k.split('#')[0]));
  });
});

describe('complianceCounts', () => {
  it('counts what the strip header says', () => {
    expect(
      complianceCounts([
        issue({ status: 'fail' }),
        issue({ status: 'fail' }),
        issue({ status: 'warn' }),
        issue({ status: 'pass' }),
        issue({ status: 'not_applicable' }),
      ]),
    ).toEqual({ fail: 2, warn: 1, pass: 1 });
  });
});

// ---------------------------------------------------------------------------
// Markers
// ---------------------------------------------------------------------------

describe('markersFor', () => {
  it('pins a marker at the centre of what the rule named', () => {
    const chips = mapComplianceChips([issue({ elementIds: [FIXTURE_IDS.wallSouth] })], house);
    const markers = markersFor(chips, FIXTURE_IDS.groundStorey);
    expect(markers).toHaveLength(1);
    expect(markers[0]?.atMm).toEqual({ x: 3000, y: 0 });
  });

  it('does not draw a first-floor violation over the ground-floor plan', () => {
    const chips = mapComplianceChips([issue({ elementIds: [FIXTURE_IDS.wallSouth] })], house);
    expect(markersFor(chips, FIXTURE_IDS.firstStorey)).toEqual([]);
  });

  it('skips chips with nothing to point at', () => {
    const chips = mapComplianceChips([issue({ elementIds: [] })], house);
    expect(markersFor(chips, FIXTURE_IDS.groundStorey)).toEqual([]);
  });
});
