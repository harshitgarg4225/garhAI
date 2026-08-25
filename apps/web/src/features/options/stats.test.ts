/**
 * Spec for the pure option derivations: key stats (Indian display), the
 * compliance badge, the Vastu wheel breakdown, signature/room grouping and the
 * compare-two diff, the §5.6 banner fallback, the solve-request builders, and
 * the assumption-chip → op round trip.
 */

import { describe, expect, it } from 'vitest';

import { fromSqft } from '@garh/model';

import {
  assumptionEditOp,
  assumptionLabel,
  assumptionValueText,
  bannerFor,
  bedroomCount,
  compareOptions,
  complianceSummary,
  diffRooms,
  effectiveBanner,
  keyStats,
  moreLikeThisParams,
  newSeedParams,
  perFloorParams,
  regenerateOthersParams,
  roomMultiset,
  vastuWheel,
} from './stats';
import {
  planOptionSchema,
  readSolveOutcome,
  solverJobDetailSchema,
  type OptionComplianceRow,
  type Placement,
  type PlanOption,
} from './types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function placement(roomType: string, storeyIndex = 0): Placement {
  return {
    roomKey: roomType,
    roomType,
    storeyIndex,
    xMm: 0,
    yMm: 0,
    widthMm: 3000,
    depthMm: 3600,
    roomId: null,
  };
}

function option(overrides: Record<string, unknown> = {}): PlanOption {
  return planOptionSchema.parse({
    id: 'opt_1',
    rank: 0,
    scores: {
      composite: 72,
      vastu: 80,
      circulationPercent: 14,
      targetAreaFit: 78,
      daylight: 66,
      furnitureFit: 90,
    },
    ops: [],
    signature: ['living:C', 'kitchen:SE'],
    stairAnchorId: 'anchor_a',
    builtUpMm2: fromSqft(1450), // exactly 1,450 sq ft as integer mm²
    footprintMm2: fromSqft(800),
    ...overrides,
  });
}

const row = (partial: Partial<OptionComplianceRow>): OptionComplianceRow => ({
  ruleId: 'nbc.room.area',
  packId: 'nbc-core',
  status: 'pass',
  title: null,
  message: null,
  cite: null,
  hard: false,
  elements: [],
  ...partial,
});

// ---------------------------------------------------------------------------

describe('keyStats — the three §15 card stats', () => {
  it('formats built-up with Indian grouping: "1,450 sq ft"', () => {
    const stats = keyStats(option());
    expect(stats.builtUpLabel).toBe('1,450 sq ft');
  });

  it('counts bedrooms across bedroom types and states furniture fit', () => {
    const stats = keyStats(
      option({
        placements: [
          placement('bedroom_master'),
          placement('bedroom'),
          placement('guest_bedroom', 1),
          placement('bath'),
        ],
      }),
    );
    expect(stats.bedrooms).toBe(3);
    expect(stats.bedroomsLabel).toBe('3 bedrooms · furniture fits');
    expect(stats.furnitureFits).toBe(true); // §5.6: gate passed or not shown
  });

  it('reads circulation from the critic, not recomputation', () => {
    expect(keyStats(option()).circulationLabel).toBe('14% circulation');
  });

  it('handles missing placements without inventing bedrooms', () => {
    expect(bedroomCount(undefined)).toBe(0);
    expect(keyStats(option()).bedroomsLabel).toBe('Furniture fits');
  });
});

describe('complianceSummary', () => {
  it('counts pass/warn/fail and hard fails separately', () => {
    const summary = complianceSummary([
      row({ status: 'pass' }),
      row({ status: 'pass' }),
      row({ status: 'warn' }),
      row({ status: 'fail' }),
      row({ status: 'fail', hard: true }),
      row({ status: 'not_applicable' }),
    ]);
    expect(summary).toEqual({ pass: 2, warn: 1, fail: 2, hardFails: 1 });
  });
});

describe('vastuWheel', () => {
  it('paints a rule onto its zone and the violation onto the actual zone', () => {
    const wheel = vastuWheel(
      option({
        compliance: [
          row({
            ruleId: 'vastu.kitchen.zone',
            packId: 'vastu',
            status: 'fail',
            actual: 'NW',
            title: 'Kitchen - zone',
          }),
          row({ ruleId: 'vastu.master.zone', packId: 'vastu', status: 'pass' }),
        ],
      }),
    );
    expect(wheel.sectors.SE).toBe('fail'); // where Vastu wants the kitchen
    expect(wheel.sectors.NW).toBe('fail'); // where it actually sits
    expect(wheel.sectors.SW).toBe('pass'); // master ok
    expect(wheel.sectors.N).toBe('none');
    expect(wheel.score).toBe(80);
    expect(wheel.applicable).toBe(true);
  });

  it('keeps the worst status when rules share a sector', () => {
    const wheel = vastuWheel(
      option({
        compliance: [
          row({ ruleId: 'vastu.pooja.zone', packId: 'vastu', status: 'pass' }),
          row({ ruleId: 'vastu.toilet.never_ne', packId: 'vastu', status: 'fail' }),
        ],
      }),
    );
    expect(wheel.sectors.NE).toBe('fail');
  });

  it('routes the brahmasthan rule to the centre', () => {
    const wheel = vastuWheel(
      option({
        compliance: [row({ ruleId: 'vastu.brahmasthan.open', packId: 'vastu', status: 'warn' })],
      }),
    );
    expect(wheel.center).toBe('warn');
  });

  it('lists unknown vastu rules instead of dropping them', () => {
    const wheel = vastuWheel(
      option({
        compliance: [row({ ruleId: 'vastu.regional.custom', packId: 'vastu', status: 'warn' })],
      }),
    );
    expect(wheel.unplaced).toHaveLength(1);
    expect(wheel.applicable).toBe(true);
  });

  it('ignores non-vastu rows and not_applicable rows; hides when nothing applies', () => {
    const wheel = vastuWheel(
      option({
        compliance: [
          row({ ruleId: 'nbc.room.area', status: 'fail' }),
          row({ ruleId: 'vastu.pooja.zone', packId: 'vastu', status: 'not_applicable' }),
        ],
      }),
    );
    expect(wheel.applicable).toBe(false);
    expect(Object.values(wheel.sectors).every((s) => s === 'none')).toBe(true);
  });
});

describe('bannerFor / effectiveBanner — §5.6 honest copy', () => {
  it('is silent at the target and for zero (zero is a failure state)', () => {
    expect(bannerFor(3)).toBeNull();
    expect(bannerFor(4)).toBeNull();
    expect(bannerFor(0)).toBeNull();
  });

  it('states the shortfall plainly', () => {
    expect(bannerFor(1)).toBe('1 strong option found for this plot');
    expect(bannerFor(2)).toBe('2 strong options found for this plot');
  });

  it("prefers the worker's own banner over the fallback", () => {
    const outcome = readSolveOutcome(
      solverJobDetailSchema.parse({
        id: 'job_1',
        status: 'succeeded',
        result: {
          options: [option()],
          banner: '2 strong options found for this plot',
        },
      }),
    );
    expect(effectiveBanner(outcome)).toBe('2 strong options found for this plot');
  });
});

describe('room grouping and the compare-two diff', () => {
  const a = option({
    placements: [
      placement('bedroom'),
      placement('bedroom'),
      placement('bedroom'),
      placement('study'),
    ],
  });
  const b = option({
    id: 'opt_2',
    rank: 1,
    stairAnchorId: 'anchor_b',
    scores: { composite: 65, vastu: 90, circulationPercent: 17 },
    builtUpMm2: fromSqft(1500),
    placements: [placement('bedroom'), placement('bedroom'), placement('pooja')],
  });

  it('groups placements into a room-type multiset', () => {
    const counts = roomMultiset(a.placements);
    expect(counts.get('bedroom')).toBe(3);
    expect(counts.get('study')).toBe(1);
  });

  it('diffs rooms as multisets — a third bedroom counts as a difference', () => {
    const diff = diffRooms(a.placements, b.placements);
    expect(diff.onlyA).toEqual(['bedroom', 'study']);
    expect(diff.onlyB).toEqual(['pooja']);
    expect(diff.shared).toEqual(['bedroom', 'bedroom']);
  });

  it('computes signed score deltas (b − a) with composite first', () => {
    const cmp = compareOptions(a, b);
    expect(cmp.scores[0]?.key).toBe('composite');
    expect(cmp.scores[0]?.delta).toBe(65 - 72);
    const vastu = cmp.scores.find((s) => s.key === 'vastu');
    expect(vastu?.delta).toBe(10);
    expect(cmp.builtUpDeltaMm2).toBe(fromSqft(1500) - fromSqft(1450));
    expect(cmp.circulationDelta).toBe(3);
    expect(cmp.sameStairAnchor).toBe(false);
  });
});

describe('solve-request builders', () => {
  it('regenerateOthers carries the locked ids (§5.7)', () => {
    expect(regenerateOthersParams(['room_a', 'room_b'])).toEqual({
      lockedRoomIds: ['room_a', 'room_b'],
    });
  });

  it('perFloor carries the floor plus the off-floor locks', () => {
    expect(perFloorParams(1, ['room_g1'])).toEqual({
      storeyIndex: 1,
      lockedRoomIds: ['room_g1'],
    });
  });

  it('moreLikeThis stays in the seed family, offset by rank', () => {
    expect(moreLikeThisParams({ seed: 42 }, option())).toEqual({
      seed: 43,
      likeOptionId: 'opt_1',
    });
    expect(moreLikeThisParams({ seed: 42 }, option({ id: 'opt_3', rank: 2 }))).toEqual({
      seed: 45,
      likeOptionId: 'opt_3',
    });
    // No recorded seed: family starts at 0, still deterministic.
    expect(moreLikeThisParams({}, option()).seed).toBe(1);
  });

  it('newSeed is a fresh 31-bit integer from the injected source', () => {
    expect(newSeedParams(() => 0.5)).toEqual({ seed: 1_073_741_823 });
    expect(newSeedParams(() => 0)).toEqual({ seed: 0 });
  });
});

describe('assumptionEditOp — chips dispatch ops, never dead text', () => {
  it('parses an area edit into a nested brief.update merge patch', () => {
    const op = assumptionEditOp('brief.rooms.bedroom2.targetAreaMm2', '120 sqft');
    expect(op).toEqual({
      type: 'brief.update',
      payload: { patch: { rooms: { bedroom2: { targetAreaMm2: 11_148_365 } } } },
    });
  });

  it('parses a length edit by the Mm suffix', () => {
    const op = assumptionEditOp('brief.floorToFloorMm', '3.05m');
    expect(op).toEqual({
      type: 'brief.update',
      payload: { patch: { floorToFloorMm: 3050 } },
    });
  });

  it('parses a bare count field as a plain integer', () => {
    expect(assumptionEditOp('brief.bedrooms', '4')).toEqual({
      type: 'brief.update',
      payload: { patch: { bedrooms: 4 } },
    });
  });

  it('refuses non-brief fields — nothing to patch, chip stays read-only', () => {
    expect(assumptionEditOp('envelope.footprintAreaMm2', '900 sqft')).toBeNull();
  });

  it('refuses unparseable and non-integer input instead of dispatching NaN', () => {
    expect(assumptionEditOp('brief.bedrooms', 'four')).toBeNull();
    expect(assumptionEditOp('brief.bedrooms', '3.5')).toBeNull();
    expect(assumptionEditOp('brief.rooms.x.targetAreaMm2', 'garbage')).toBeNull();
  });
});

describe('assumption chip display helpers', () => {
  it('formats values by the field unit suffix', () => {
    expect(assumptionValueText('brief.rooms.x.targetAreaMm2', 11_148_365)).toBe('120 sq ft');
    expect(assumptionValueText('brief.floorToFloorMm', 3050)).toContain("'");
    expect(assumptionValueText('brief.bedrooms', 3)).toBe('3');
    expect(assumptionValueText('brief.style', 'contemporary')).toBe('contemporary');
  });

  it('labels a dotted field readably', () => {
    expect(assumptionLabel('brief.rooms.bedroom2.targetAreaMm2')).toBe('Bedroom 2 · Target Area');
  });
});
