/**
 * "Fix it" must do exactly one thing when pressed and be absent when it cannot.
 *
 * The negative controls are the point of this file. `hasClientAutofix` decides
 * whether a button exists at all; the rows below pin that a pack claiming a fix
 * the client cannot compute produces NO button — the alternative is a strip full
 * of buttons that do nothing, which teaches an architect the strip is decorative.
 * The store-level test proves a plan lands as ONE undo entry, so "Undo" after
 * "Fix it" restores the document in a single step.
 */

import { fold, makeTwoRoomPlanWithOpenings, FIXTURE_IDS } from '@garh/model';
import type { Op, ProjectDoc } from '@garh/model';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { toComplianceIssue } from '../../pages/_contracts';
import { useModelStore } from '../../stores/model';
import { computeAutofix, hasClientAutofix } from './autofix';
import type { ComplianceIssueVM } from '../../components';

// The model store flushes op groups to the API; none of these tests care what
// the server says, only that a plan is one undo entry.
vi.mock('../../lib/api', () => ({
  api: {
    ops: {
      model: vi.fn(),
      append: vi.fn(() => new Promise(() => undefined)),
      since: vi.fn(),
    },
  },
}));

function withStairAndBalcony(): ProjectDoc {
  let doc = makeTwoRoomPlanWithOpenings();
  const stair: Op = {
    type: 'stair.add',
    payload: {
      id: FIXTURE_IDS.stair,
      storeyId: FIXTURE_IDS.groundStorey,
      kind: 'dogleg',
      origin: { x: 1000, y: 1000 },
      direction: 'N',
      // 16 × 188 = 3,008 mm: within the model's ±10 mm of the 3,000 mm storey.
      riserMm: 188,
      treadMm: 230,
      widthMm: 850,
      risersCount: 16,
      landing: null,
    },
  };
  const balcony: Op = {
    type: 'balcony.set',
    payload: {
      action: 'add',
      id: FIXTURE_IDS.balcony,
      storeyId: FIXTURE_IDS.groundStorey,
      polygon: [
        { x: 0, y: -1500 },
        { x: 2400, y: -1500 },
        { x: 2400, y: 0 },
        { x: 0, y: 0 },
      ],
      railingKind: 'ms',
      railingHeightMm: 1100,
      projectionMm: 1500,
      slabThicknessMm: 125,
    },
  };
  doc = fold(doc, stair).model;
  doc = fold(doc, balcony).model;
  return doc;
}

function issue(
  overrides: Partial<ComplianceIssueVM> & Pick<ComplianceIssueVM, 'ruleId'>,
): ComplianceIssueVM {
  return {
    status: 'fail',
    message: overrides.ruleId,
    elementIds: [],
    fixAvailable: true,
    ...overrides,
  };
}

describe('hasClientAutofix — the button exists only when a press does something', () => {
  it('knows the strategies this client computes', () => {
    expect(hasClientAutofix({ strategy: 'raise-storey-height' }, 'ceiling_height_min')).toBe(true);
    expect(hasClientAutofix({ strategy: 'resize-opening-to-limit' }, 'opening_width_min')).toBe(
      true,
    );
    expect(hasClientAutofix({ strategy: 'adjust-stair-parameters' }, 'stair_riser_max')).toBe(true);
    expect(hasClientAutofix({ strategy: 'trim-projection' }, 'projection_max')).toBe(true);
  });

  it('NEGATIVE: a strategy the client cannot compute yields no button', () => {
    // The nbc-core packs mark these computable; the client cannot honestly do them.
    expect(hasClientAutofix({ strategy: 'grow-room-to-limit' }, 'room_area_min')).toBe(false);
    expect(hasClientAutofix({ strategy: 'widen-room-to-limit' }, 'room_width_min')).toBe(false);
    expect(
      hasClientAutofix({ strategy: 'enlarge-openings-to-ratio' }, 'ventilation_ratio_min'),
    ).toBe(false);
    expect(hasClientAutofix({ strategy: 'declare-rwh' }, 'custom')).toBe(false);
    // One strategy, four checks — headroom is a storey fact, not a stair parameter.
    expect(hasClientAutofix({ strategy: 'adjust-stair-parameters' }, 'headroom_min')).toBe(false);
    expect(hasClientAutofix(null, 'ceiling_height_min')).toBe(false);
    expect(hasClientAutofix({ strategy: 'raise-storey-height' }, null)).toBe(false);
  });

  it('NEGATIVE: the VM flag is the engine claim AND the client ability', () => {
    const base = {
      ruleId: 'nbc.ceiling.habitable.min',
      status: 'fail' as const,
      message: 'too low',
      checkType: 'ceiling_height_min',
    };
    const computable = toComplianceIssue({
      ...base,
      fixAvailable: true,
      autofix: { opType: 'storey.set_height', strategy: 'raise-storey-height' },
    });
    expect(computable.fixAvailable).toBe(true);

    // The pack says computable:false → engine says fixAvailable:false → no button.
    const packSaysNo = toComplianceIssue({
      ...base,
      fixAvailable: false,
      autofix: { opType: 'storey.set_height', strategy: 'raise-storey-height' },
    });
    expect(packSaysNo.fixAvailable).toBe(false);

    // The pack claims a fix the client cannot build → no button either.
    const clientSaysNo = toComplianceIssue({
      ruleId: 'nbc.room.habitable.area.min',
      status: 'fail',
      message: 'too small',
      checkType: 'room_area_min',
      fixAvailable: true,
      autofix: { opType: 'wall.move', strategy: 'grow-room-to-limit' },
    });
    expect(clientSaysNo.fixAvailable).toBe(false);

    // No autofix block at all — the common case for 80+ rules.
    expect(toComplianceIssue({ ...base, fixAvailable: true }).fixAvailable).toBe(false);
  });
});

describe('computeAutofix — one honest op group per strategy', () => {
  const doc = withStairAndBalcony();
  const room = doc.house.rooms[0];
  if (room === undefined) throw new Error('fixture has no detected room');

  it('raise-storey-height: raises the room’s storey by the shortfall, not to the limit', () => {
    const storey = doc.house.storeys.find((s) => s.id === room.storeyId);
    if (storey === undefined) throw new Error('room has no storey');
    const plan = computeAutofix(
      issue({
        ruleId: 'nbc.ceiling.habitable.min',
        checkType: 'ceiling_height_min',
        autofix: { opType: 'storey.set_height', strategy: 'raise-storey-height' },
        unit: 'mm',
        limit: 2750,
        instances: [
          { elementId: room.id, label: room.name, status: 'fail', actual: 2600, limit: 2750 },
        ],
      }),
      doc,
    );
    expect(plan).not.toBeNull();
    expect(plan?.ops).toEqual([
      {
        type: 'storey.set_height',
        payload: { storeyId: storey.id, heightMm: storey.heightMm + 150 },
      },
    ]);
    expect(plan?.label).toBe('Storey height raised');
  });

  it('raise-storey-height: two failing rooms on one storey collapse to ONE op (largest shortfall)', () => {
    const rooms = doc.house.rooms;
    const [a, b] = rooms;
    if (a === undefined || b === undefined) throw new Error('fixture has fewer than two rooms');
    const storey = doc.house.storeys.find((s) => s.id === a.storeyId);
    if (storey === undefined) throw new Error('no storey');
    const plan = computeAutofix(
      issue({
        ruleId: 'nbc.ceiling.habitable.min',
        checkType: 'ceiling_height_min',
        autofix: { opType: 'storey.set_height', strategy: 'raise-storey-height' },
        limit: 2750,
        instances: [
          { elementId: a.id, label: a.name, status: 'fail', actual: 2700, limit: 2750 },
          { elementId: b.id, label: b.name, status: 'fail', actual: 2500, limit: 2750 },
        ],
      }),
      doc,
    );
    expect(plan?.ops).toHaveLength(1);
    expect(plan?.ops[0]?.payload).toEqual({ storeyId: storey.id, heightMm: storey.heightMm + 250 });
  });

  it('resize-opening-to-limit: widens the offending door to the limit and leaves passing ones alone', () => {
    const plan = computeAutofix(
      issue({
        ruleId: 'nbc.door.main.width.min',
        checkType: 'opening_width_min',
        autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
        limit: 1000,
        instances: [
          {
            elementId: FIXTURE_IDS.doorMain,
            label: 'Main door',
            status: 'fail',
            actual: 900,
            limit: 1000,
          },
          {
            elementId: FIXTURE_IDS.windowWest,
            label: 'Window',
            status: 'pass',
            actual: 1200,
            limit: 1000,
          },
        ],
      }),
      doc,
    );
    expect(plan?.ops).toEqual([
      { type: 'opening.resize', payload: { openingId: FIXTURE_IDS.doorMain, widthMm: 1000 } },
    ]);
  });

  it('adjust-stair-parameters: riser comes DOWN to a max by re-counting the flight; tread and width go UP to a min', () => {
    const riser = computeAutofix(
      issue({
        ruleId: 'nbc.stair.riser.max',
        checkType: 'stair_riser_max',
        autofix: { opType: 'stair.edit', strategy: 'adjust-stair-parameters' },
        limit: 175,
        elementIds: [FIXTURE_IDS.stair],
        actual: 188,
      }),
      doc,
    );
    // 3,000 mm storey ÷ 175 max → 18 risers of 167 mm (18 × 167 = 3,006, inside ±10).
    // Setting riserMm alone would leave 16 × 175 = 2,800 and be rejected by the model.
    expect(riser?.ops).toEqual([
      {
        type: 'stair.edit',
        payload: { stairId: FIXTURE_IDS.stair, patch: { riserMm: 167, risersCount: 18 } },
      },
    ]);
    const folded = fold(doc, riser?.ops[0] as Op);
    expect(folded.model.house.stairs[0]?.riserMm).toBe(167);
    const tread = computeAutofix(
      issue({
        ruleId: 'nbc.stair.tread.min',
        checkType: 'stair_tread_min',
        autofix: { opType: 'stair.edit', strategy: 'adjust-stair-parameters' },
        limit: 250,
        elementIds: [FIXTURE_IDS.stair],
      }),
      doc,
    );
    expect(tread?.ops[0]?.payload).toEqual({ stairId: FIXTURE_IDS.stair, patch: { treadMm: 250 } });
    const width = computeAutofix(
      issue({
        ruleId: 'nbc.stair.width.min',
        checkType: 'stair_width_min',
        autofix: { opType: 'stair.edit', strategy: 'adjust-stair-parameters' },
        limit: 900,
        elementIds: [FIXTURE_IDS.stair],
      }),
      doc,
    );
    expect(width?.ops[0]?.payload).toEqual({ stairId: FIXTURE_IDS.stair, patch: { widthMm: 900 } });
  });

  it('trim-projection: pulls the balcony back to the limit', () => {
    const plan = computeAutofix(
      issue({
        ruleId: 'blr.projection.balcony.front',
        checkType: 'projection_max',
        autofix: { opType: 'balcony.edit', strategy: 'trim-projection' },
        limit: 1200,
        instances: [
          {
            elementId: FIXTURE_IDS.balcony,
            label: 'Balcony',
            status: 'fail',
            actual: 1500,
            limit: 1200,
          },
        ],
      }),
      doc,
    );
    expect(plan?.ops).toEqual([
      {
        type: 'balcony.set',
        payload: { action: 'edit', id: FIXTURE_IDS.balcony, projectionMm: 1200 },
      },
    ]);
  });

  it('NEGATIVE: an element gone since the last check yields null, not an op the model rejects', () => {
    const plan = computeAutofix(
      issue({
        ruleId: 'nbc.door.main.width.min',
        checkType: 'opening_width_min',
        autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
        limit: 1000,
        elementIds: ['opening_01J000000000000000000000GONE'],
      }),
      doc,
    );
    expect(plan).toBeNull();
  });

  it('NEGATIVE: a non-numeric limit, or nothing failing, yields null', () => {
    expect(
      computeAutofix(
        issue({
          ruleId: 'nbc.door.main.width.min',
          checkType: 'opening_width_min',
          autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
          limit: 'wide enough',
          elementIds: [FIXTURE_IDS.doorMain],
        }),
        doc,
      ),
    ).toBeNull();
    expect(
      computeAutofix(
        issue({
          ruleId: 'nbc.door.main.width.min',
          checkType: 'opening_width_min',
          autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
          limit: 1000,
          instances: [
            {
              elementId: FIXTURE_IDS.doorMain,
              label: 'Main door',
              status: 'pass',
              actual: 1200,
              limit: 1000,
            },
          ],
        }),
        doc,
      ),
    ).toBeNull();
  });

  it('NEGATIVE: an uncomputable strategy yields null even when the pack says computable', () => {
    expect(
      computeAutofix(
        issue({
          ruleId: 'nbc.room.habitable.area.min',
          checkType: 'room_area_min',
          autofix: { opType: 'wall.move', strategy: 'grow-room-to-limit' },
          limit: 9_500_000,
          instances: [
            {
              elementId: room.id,
              label: room.name,
              status: 'fail',
              actual: 8_900_000,
              limit: 9_500_000,
            },
          ],
        }),
        doc,
      ),
    ).toBeNull();
  });
});

describe('a plan lands in the model store as ONE undo group', () => {
  beforeEach(() => {
    useModelStore.getState().reset();
  });

  it('dispatching the two-op plan adds one history entry, and undo restores both', () => {
    // Seed the store with a document straight from the fixture; the store's
    // `reset()` leaves an empty doc, so put the fixture in through the state.
    const doc = withStairAndBalcony();
    useModelStore.setState({ doc, serverDoc: doc, status: 'ready', projectId: 'p1' });
    const [a, b] = doc.house.rooms;
    if (a === undefined || b === undefined) throw new Error('fixture has fewer than two rooms');
    // Two strategies' worth of ops in one plan is not something the packs emit,
    // but the store must treat WHATEVER computeAutofix returns as one group.
    const plan = computeAutofix(
      issue({
        ruleId: 'nbc.door.main.width.min',
        checkType: 'opening_width_min',
        autofix: { opType: 'opening.resize', strategy: 'resize-opening-to-limit' },
        limit: 1000,
        // Per-instance limits (a value override can move one and not the other).
        instances: [
          {
            elementId: FIXTURE_IDS.doorMain,
            label: 'Main door',
            status: 'fail',
            actual: 900,
            limit: 1000,
          },
          {
            elementId: FIXTURE_IDS.windowWest,
            label: 'Window',
            status: 'fail',
            actual: 1200,
            limit: 1500,
          },
        ],
      }),
      doc,
    );
    expect(plan?.ops).toHaveLength(2);
    if (plan === null) throw new Error('no plan');

    const before = useModelStore.getState().undoStack.length;
    const result = useModelStore.getState().dispatch(plan.ops, { label: plan.label });
    expect(result.ok).toBe(true);
    const state = useModelStore.getState();
    expect(state.undoStack.length).toBe(before + 1);
    expect(state.doc.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain)?.widthMm).toBe(1000);
    expect(state.doc.house.openings.find((o) => o.id === FIXTURE_IDS.windowWest)?.widthMm).toBe(
      1500,
    );

    expect(useModelStore.getState().undo()).toBe(true);
    const after = useModelStore.getState().doc;
    expect(after.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain)?.widthMm).toBe(
      doc.house.openings.find((o) => o.id === FIXTURE_IDS.doorMain)?.widthMm,
    );
    expect(after.house.openings.find((o) => o.id === FIXTURE_IDS.windowWest)?.widthMm).toBe(
      doc.house.openings.find((o) => o.id === FIXTURE_IDS.windowWest)?.widthMm,
    );
  });
});
