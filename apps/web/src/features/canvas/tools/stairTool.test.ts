/**
 * Spec for S — the stair tool, and the flight solver behind it.
 *
 * The load-bearing constraint is the model core's, not the NBC's:
 *
 *     | risersCount × riserMm − storeyHeightMm | ≤ 10 mm
 *
 * Because risers are integer millimetres, most riser counts cannot satisfy it,
 * so `solveFlight` searches. Every flight it returns is asserted here to be one
 * the REAL `validate.ts` accepts — a stair tool that emits a rejected op is a
 * stair tool that has taught the architect to distrust the preview.
 *
 * The NBC numbers (riser ≤ 190, tread ≥ 250, width ≥ 900, headroom ≥ 2100) are
 * ADVISORY here and are asserted to be chips, never blocks: compliance never
 * blocks, it informs (golden rule 5).
 */

import { describe, expect, it } from 'vitest';

import { validateOpAgainstDoc, validateOpShape, type StairKind } from '@garh/model';

import {
  COMFORT_2R_T_MAX_MM,
  COMFORT_2R_T_MIN_MM,
  NBC_HEADROOM_MIN_MM,
  NBC_RISER_MAX_MM,
  NBC_STAIR_WIDTH_MIN_MM,
  NBC_TREAD_MIN_MM,
  STAIR_RISE_TOLERANCE_MM,
} from './constants';
import {
  comfortTreadMm,
  flightIssues,
  landingFor,
  solveFlight,
  STAIR_WELL_GAP_MM,
  type FlightSolution,
} from './stairFlight';
import { StairTool } from './stairTool';
import {
  chipIds,
  FIXTURE_IDS,
  key,
  makeCtx,
  nthId,
  opOfType,
  ptr,
  readout,
  typeText,
  withOps,
} from './toolTestKit';

const STOREY_HEIGHT_MM = 3000;

interface FlightOver {
  readonly storeyHeightMm?: number;
  readonly kind?: StairKind;
  readonly widthMm?: number;
  readonly treadMm?: number;
  readonly slabThicknessMm?: number;
}

/** The default flight: a 3000 mm storey, 900 mm dogleg, 150 mm slab above. */
function flight(over: FlightOver = {}): FlightSolution {
  const result = solveFlight({
    storeyHeightMm: over.storeyHeightMm ?? STOREY_HEIGHT_MM,
    kind: over.kind ?? 'dogleg',
    widthMm: over.widthMm ?? 900,
    preferredRiserMm: 165,
    slabThicknessMm: over.slabThicknessMm ?? 150,
    ...(over.treadMm === undefined ? {} : { treadMm: over.treadMm }),
  });
  if (!result.ok) throw new Error(`expected a flight: ${result.failure.reason}`);
  return result.flight;
}

// ---------------------------------------------------------------------------
// The flight solver
// ---------------------------------------------------------------------------

describe('solveFlight', () => {
  it('lands on the floor above within the model’s ±10 mm invariant', () => {
    const f = flight();
    expect(Math.abs(f.totalRiseMm - STOREY_HEIGHT_MM)).toBeLessThanOrEqual(STAIR_RISE_TOLERANCE_MM);
    expect(f.totalRiseMm).toBe(f.risersCount * f.riserMm);
    expect(f.riseErrorMm).toBe(f.totalRiseMm - STOREY_HEIGHT_MM);
  });

  it('picks 18 × 167 for a 3000 mm storey — comfort first, exactness second', () => {
    const f = flight();
    expect(f.risersCount).toBe(18);
    expect(f.riserMm).toBe(167);
    expect(f.treadMm).toBe(290);
    expect(f.riseErrorMm).toBe(6);
  });

  it('is deterministic — the same inputs give the identical flight', () => {
    expect(flight()).toEqual(flight());
  });

  it('keeps the riser inside the NBC limit for every storey height it accepts', () => {
    for (let height = 2400; height <= 3600; height += 1) {
      const result = solveFlight({ storeyHeightMm: height, kind: 'straight', widthMm: 900 });
      if (!result.ok) continue;
      const f = result.flight;
      expect(f.riserMm).toBeLessThanOrEqual(NBC_RISER_MAX_MM);
      expect(Math.abs(f.riserMm * f.risersCount - height)).toBeLessThanOrEqual(
        STAIR_RISE_TOLERANCE_MM,
      );
      expect(Number.isInteger(f.riserMm)).toBe(true);
      expect(Number.isInteger(f.treadMm)).toBe(true);
    }
  });

  it('counts treads as one fewer than risers — the last riser lands on the slab', () => {
    const straight = flight({ kind: 'straight' });
    expect(straight.risersToLanding).toBe(straight.risersCount);
    expect(straight.goingMm).toBe((straight.risersCount - 1) * straight.treadMm);
  });

  it('splits a dogleg at the halfway landing', () => {
    const f = flight({ kind: 'dogleg' });
    expect(f.risersToLanding).toBe(Math.ceil(f.risersCount / 2));
    expect(f.goingMm).toBe((f.risersToLanding - 1) * f.treadMm);
  });

  it('takes an explicit tread from the inspector instead of the comfort rule', () => {
    expect(flight({ treadMm: 300 }).treadMm).toBe(300);
  });

  it('says so, in plain words, when no flight fits', () => {
    const result = solveFlight({ storeyHeightMm: 8000, kind: 'straight', widthMm: 900 });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.failure.reason).toContain('8000');
    expect(result.failure.fix).toBeTruthy();
  });

  it('refuses a storey with no height rather than inventing one', () => {
    const result = solveFlight({ storeyHeightMm: 0, kind: 'straight', widthMm: 900 });
    expect(result.ok).toBe(false);
  });
});

describe('landings match what fold cuts out of the slab', () => {
  it('has none for a straight flight', () => {
    expect(landingFor('straight', 900)).toBeNull();
  });

  it('spans both flights of a dogleg or U, with one brick module between them', () => {
    expect(landingFor('dogleg', 900)).toEqual({ widthMm: 1915, depthMm: 900 });
    expect(landingFor('U', 1000)).toEqual({ widthMm: 2115, depthMm: 1000 });
    expect(STAIR_WELL_GAP_MM).toBe(115);
  });

  it('is the width of the return leg for an L', () => {
    expect(landingFor('L', 900)).toEqual({ widthMm: 900, depthMm: 900 });
  });
});

describe('comfortTreadMm', () => {
  it('lands 2R + T inside the comfort band', () => {
    for (const riser of [140, 150, 165, 175, 190]) {
      const tread = comfortTreadMm(riser);
      const value = 2 * riser + tread;
      expect(value).toBeGreaterThanOrEqual(COMFORT_2R_T_MIN_MM);
      expect(value).toBeLessThanOrEqual(COMFORT_2R_T_MAX_MM);
      expect(tread % 5).toBe(0);
    }
  });

  it('never returns a tread under the NBC minimum', () => {
    expect(comfortTreadMm(190)).toBeGreaterThanOrEqual(NBC_TREAD_MIN_MM);
  });
});

describe('flightIssues — advisory, cited, with a fix', () => {
  it('is silent about a healthy flight', () => {
    expect(flightIssues(flight(), 900)).toEqual([]);
  });

  it('raises the width rule when the flight is narrow', () => {
    const issues = flightIssues(flight(), 800);
    expect(issues.map((i) => i.id)).toEqual(['nbc.stair.width.min']);
    expect(issues[0]?.severity).toBe('error');
    expect(issues[0]?.text).toContain(String(NBC_STAIR_WIDTH_MIN_MM));
    expect(issues[0]?.cite).toBe('Part 4, Cl. 4.4.2');
    expect(issues[0]?.fix).toBeTruthy();
  });

  it('warns about headroom under a low floor-to-floor', () => {
    const low = flight({ storeyHeightMm: 2200, kind: 'straight' });
    expect(low.headroomMm).toBe(2050);
    const issues = flightIssues(low, 900);
    expect(issues.map((i) => i.id)).toEqual(['nbc.stair.headroom.min']);
    expect(issues[0]?.severity).toBe('warning');
    expect(issues[0]?.text).toContain(String(NBC_HEADROOM_MIN_MM));
  });

  it('labels the comfort rule as a rule of thumb, not as code', () => {
    const cramped = flight({ treadMm: 200 });
    const comfort = flightIssues(cramped, 900).find((i) => i.id === 'comfort.2r-t');
    expect(comfort?.severity).toBe('info');
    expect(comfort?.cite).toContain('not a code requirement');
  });

  it('every issue carries a citation and a fix, so a chip is never a dead end', () => {
    for (const issue of flightIssues(flight({ treadMm: 200 }), 700)) {
      expect(issue.cite).toBeTruthy();
      expect(issue.fix).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// The tool
// ---------------------------------------------------------------------------

describe('the stair tool', () => {
  it('starts idle and has nothing to commit', () => {
    const tool = new StairTool();
    const ctx = makeCtx();
    expect(tool.phase).toBe('idle');
    expect(tool.commit(ctx)).toBeNull();
  });

  it('moves to preview as soon as the pointer is over the storey', () => {
    const tool = new StairTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(tool.phase).toBe('preview');
    const shape = tool.preview(ctx).shape;
    expect(shape.kind).toBe('stair');
    if (shape.kind !== 'stair') return;
    expect(shape.risersCount).toBe(18);
    expect(shape.riserMm).toBe(167);
    expect(shape.footprint.length).toBeGreaterThanOrEqual(4);
    // 9 risers to the landing means 8 tread lines drawn before it.
    expect(shape.treads).toHaveLength(8);
    expect(shape.arrow).not.toBeNull();
  });

  it('emits a stair.add the real validator accepts', () => {
    const ctx = makeCtx();
    const tool = new StairTool();
    const response = tool.onPointerDown(ctx, ptr(1150, 1150));

    const op = opOfType(response.commit?.ops[0], 'stair.add');
    expect(op.payload).toEqual({
      id: nthId('stair', 1),
      storeyId: FIXTURE_IDS.groundStorey,
      kind: 'dogleg',
      origin: { x: 1150, y: 1150 },
      direction: 'N',
      riserMm: 167,
      treadMm: 290,
      widthMm: 900,
      risersCount: 18,
      landing: { widthMm: 1915, depthMm: 900 },
    });
    expect(validateOpShape(op)).toEqual([]);
    expect(validateOpAgainstDoc(ctx.doc, op)).toEqual([]);
    expect(response.commit?.label).toBe('Stair added');
    expect(tool.phase).toBe('idle');
  });

  it('shows the flight, tread, going and type before anything is committed', () => {
    const tool = new StairTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    const preview = tool.preview(ctx);
    expect(readout(preview, 'flight')).toBe('18R × 167 mm');
    expect(readout(preview, 'kind')).toBe('dogleg · up N');
    expect(readout(preview, 'rise')).toBe('+6 mm');
    expect(readout(preview, 'going')).not.toBeNull();
  });

  it('raises no chip for a compliant flight', () => {
    const tool = new StairTool();
    const ctx = makeCtx();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(chipIds(tool.preview(ctx))).toEqual([]);
  });

  it('chips a narrow flight but still lets it be committed — compliance informs', () => {
    const ctx = makeCtx({ settings: { stairWidthMm: 800 } });
    const tool = new StairTool();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(chipIds(tool.preview(ctx))).toContain('nbc.stair.width.min');
    expect(tool.preview(ctx).blocked).toBeNull();
    expect(tool.commit(ctx)).not.toBeNull();
  });

  it('blocks with the reason when no flight fits the storey height', () => {
    const doc = withOps(makeCtx().doc, [
      { type: 'storey.set_height', payload: { storeyId: FIXTURE_IDS.groundStorey, heightMm: 8000 } },
    ]);
    const ctx = makeCtx({ doc });
    const tool = new StairTool();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    expect(tool.preview(ctx).blocked?.message).toContain('8000');
    expect(tool.preview(ctx).shape.kind).toBe('none');
    expect(tool.commit(ctx)).toBeNull();
  });
});

describe('stair tool keys', () => {
  it('X turns the flight through the four directions', () => {
    const tool = new StairTool();
    expect(tool.wantsKey(key('x'))).toBe(true);
    expect(tool.onKey(makeCtx(), key('x')).settingsPatch).toEqual({ stairDirection: 'E' });
    expect(
      tool.onKey(makeCtx({ settings: { stairDirection: 'W' } }), key('x')).settingsPatch,
    ).toEqual({ stairDirection: 'N' });
  });

  it('[ and ] cycle straight / dogleg / L / U', () => {
    const tool = new StairTool();
    const ctx = makeCtx(); // starts on 'dogleg'
    expect(tool.onKey(ctx, key(']')).settingsPatch).toEqual({ stairKind: 'L' });
    expect(tool.onKey(ctx, key('[')).settingsPatch).toEqual({ stairKind: 'straight' });
  });

  it('a typed width re-solves the flight and becomes the next default', () => {
    const ctx = makeCtx();
    const tool = new StairTool();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    typeText(tool, ctx, '1050');

    const response = tool.onKey(ctx, key('Enter'));
    const op = opOfType(response.commit?.ops[0], 'stair.add');
    expect(op.payload.widthMm).toBe(1050);
    // The landing spans both flights, so it grows with the width.
    expect(op.payload.landing).toEqual({ widthMm: 2215, depthMm: 1050 });
    expect(response.settingsPatch).toEqual({ stairWidthMm: 1050 });
  });

  it('a typed tread reaches the flight through Tab', () => {
    const ctx = makeCtx();
    const tool = new StairTool();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    tool.onKey(ctx, key('Tab'));
    typeText(tool, ctx, '300');
    const op = opOfType(tool.onKey(ctx, key('Enter')).commit?.ops[0], 'stair.add');
    expect(op.payload.treadMm).toBe(300);
    expect(op.payload.riserMm).toBe(167);
  });

  it('Esc drops the placement without emitting anything', () => {
    const ctx = makeCtx();
    const tool = new StairTool();
    tool.onPointerMove(ctx, ptr(1150, 1150));
    const response = tool.onKey(ctx, key('Escape'));
    expect(response.handled).toBe(true);
    expect(response.commit ?? null).toBeNull();
    expect(tool.phase).toBe('idle');
  });
});
