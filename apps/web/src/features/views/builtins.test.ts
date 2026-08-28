/**
 * Spec for the three computed views, against the real fixture document.
 *
 * The interesting assertions are the ones that show the three names mean three
 * DIFFERENT things. "Fit all" that silently equals "fit storey" is a menu with
 * a decoy in it, and nothing about the code would say so.
 */

import { describe, expect, it } from 'vitest';

import {
  DEMO_PLOT_POLYGON,
  FIXTURE_IDS,
  makeEmptyDoc,
  makeTwoRoomPlanWithOpenings,
} from '@garh/model';

import { builtInExtent, builtInViews, type BuiltInInput } from './builtins';

const DOC = makeTwoRoomPlanWithOpenings();
const GROUND = FIXTURE_IDS.groundStorey;

function input(overrides: Partial<BuiltInInput> = {}): BuiltInInput {
  return {
    house: DOC.house,
    plotBoundary: DOC.plot.boundary,
    activeStoreyId: GROUND,
    selectionIds: [],
    ...overrides,
  };
}

describe('fit all', () => {
  it('frames the plot AND the building, not either alone', () => {
    const extent = builtInExtent('fitAll', input());
    expect(extent).not.toBe(null);
    if (extent === null) return;

    // The fixture plot is 9144 × 12192; the fixture building is 6000 × 4000
    // with 230 mm external walls, so it pokes 115 mm past the origin on the
    // south and west. "All" has to contain both.
    expect(extent.box.minX).toBe(-115);
    expect(extent.box.minY).toBe(-115);
    expect(extent.box.maxX).toBe(9144);
    expect(extent.box.maxY).toBe(12_192);
    expect(extent.heightMm).toBeGreaterThan(0);
  });

  it('is a genuinely different box from fit storey', () => {
    // The distinction the two names promise. If someone later redefines "fit
    // all" as the active storey, this is what says so.
    const all = builtInExtent('fitAll', input());
    const storey = builtInExtent('fitStorey', input());
    expect(all).not.toEqual(storey);
    expect(all?.box.maxY).toBeGreaterThan(storey?.box.maxY ?? 0);
  });

  it('still frames the plot when nothing is built yet', () => {
    const empty = makeEmptyDoc();
    const extent = builtInExtent(
      'fitAll',
      input({ house: empty.house, plotBoundary: DEMO_PLOT_POLYGON, activeStoreyId: null }),
    );
    expect(extent?.box).toEqual({ minX: 0, minY: 0, maxX: 9144, maxY: 12_192 });
  });

  it('answers null — with a reason — on a project with nothing in it at all', () => {
    const empty = makeEmptyDoc();
    const specs = builtInViews(
      input({ house: empty.house, plotBoundary: [], activeStoreyId: null }),
    );
    const all = specs.find((spec) => spec.id === 'fitAll');
    expect(all?.extent).toBe(null);
    expect(all?.reason).toMatch(/nothing drawn/i);
  });
});

describe('fit storey', () => {
  it('frames the storey on screen', () => {
    const extent = builtInExtent('fitStorey', input());
    expect(extent?.box).toEqual({ minX: 0, minY: 0, maxX: 6000, maxY: 4000 });
    // The height is that storey's floor-to-floor, not the whole building's.
    expect(extent?.heightMm).toBe(DOC.house.storeys[0]?.heightMm);
  });

  it('falls back to the plot while a storey is still empty', () => {
    const extent = builtInExtent('fitStorey', input({ activeStoreyId: FIXTURE_IDS.firstStorey }));
    expect(extent?.box).toEqual({ minX: 0, minY: 0, maxX: 9144, maxY: 12_192 });
  });
});

describe('fit selection', () => {
  it('frames the picked wall, and nothing else', () => {
    const extent = builtInExtent('fitSelection', input({ selectionIds: [FIXTURE_IDS.wallSpine] }));
    expect(extent).not.toBe(null);
    if (extent === null) return;
    // The spine wall runs (3000,0)–(3000,4000) and is 115 mm thick, so its
    // ring is 57.5 mm either side — half-away-from-zero rounding gives 58.
    expect(extent.box.minX).toBeGreaterThan(2900);
    expect(extent.box.maxX).toBeLessThan(3100);
    expect(extent.box.minY).toBe(0);
    expect(extent.box.maxY).toBe(4000);
  });

  it('is unavailable, with the reason said out loud, when nothing is picked', () => {
    const spec = builtInViews(input()).find((candidate) => candidate.id === 'fitSelection');
    expect(spec?.extent).toBe(null);
    expect(spec?.reason).toBe('Nothing is selected');
  });

  it('ignores ids that are not in the model rather than framing the origin', () => {
    // A stale selection after an undo. Framing (0,0) would yank the camera to
    // the plot corner for no reason the user could explain.
    expect(builtInExtent('fitSelection', input({ selectionIds: ['wall_gone'] }))).toBe(null);
  });

  it('gives the selection a one-storey height, not the whole building', () => {
    const extent = builtInExtent('fitSelection', input({ selectionIds: [FIXTURE_IDS.wallSpine] }));
    const all = builtInExtent('fitAll', input());
    expect(extent?.heightMm).toBe(DOC.house.storeys[0]?.heightMm);
    // The building is at least as tall as one storey — usually taller. A
    // selection framed against that would stand the camera too far back.
    expect(all?.heightMm).toBeGreaterThanOrEqual(extent?.heightMm ?? 0);
  });
});

describe('builtInViews', () => {
  it('returns all three, labelled, in a fixed order', () => {
    const specs = builtInViews(input());
    expect(specs.map((spec) => spec.id)).toEqual(['fitAll', 'fitSelection', 'fitStorey']);
    expect(specs.map((spec) => spec.label)).toEqual(['Fit all', 'Fit selection', 'Fit storey']);
  });

  it('carries a reason exactly when there is nothing to frame', () => {
    for (const spec of builtInViews(input({ selectionIds: [FIXTURE_IDS.wallSouth] }))) {
      expect(spec.extent === null).toBe(spec.reason !== null);
    }
    for (const spec of builtInViews(input())) {
      expect(spec.extent === null).toBe(spec.reason !== null);
    }
  });
});
