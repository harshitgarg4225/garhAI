/**
 * The Phase-5 `three` slice: storey visibility, the applied-facade mirror and
 * the rebuild telemetry.
 *
 * The mirror is the part that can go wrong quietly. It follows the model store
 * by SUBSCRIPTION (module scope, so it stays true with no 3D component
 * mounted), and a mirror that drifted would have the top bar naming a kit the
 * fold no longer contains. So the specs here drive the REAL `useModelStore`
 * with `setState` document swaps — the same signal a dispatch/fold produces —
 * and assert the mirror follows, prunes, and never writes the model back.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { emptyProjectDoc, type ProjectDoc } from '@garh/model';

import { useModelStore } from './model';
import { appliedFacadeOf, useThreeStore } from './three';

const S0 = 'storey_01HZY0000000000000000000';
const S1 = 'storey_01HZY0000000000000000001';
const COMP = 'facadecomp_01HZY000000000000000AA';

function docWith(mutate: (doc: {
  house: {
    storeys: { id: string; name: string; heightMm: number }[];
    facade: {
      kitId: string | null;
      seed: number;
      colorwayId: string | null;
      components: unknown[];
    };
  };
}) => void): ProjectDoc {
  // Deep-clone the empty doc so each spec owns a fresh, independent document —
  // the stores compare by identity and a shared mutable doc would alias state
  // across specs.
  const doc = JSON.parse(JSON.stringify(emptyProjectDoc())) as ProjectDoc;
  mutate(doc as unknown as Parameters<typeof mutate>[0]);
  return doc;
}

beforeEach(() => {
  useThreeStore.setState({
    visibleStoreyId: null,
    appliedFacade: { kitId: null, seed: 0, colorwayId: null, componentCount: 0 },
    engineStatus: 'idle',
    engineDetail: null,
    lastRebuild: null,
  });
  useModelStore.setState({ doc: emptyProjectDoc() });
});

describe('storey visibility', () => {
  it('shows one storey, toggles back to all, and set is idempotent', () => {
    const s = useThreeStore.getState();
    s.setVisibleStorey(S0);
    expect(useThreeStore.getState().visibleStoreyId).toBe(S0);

    const before = useThreeStore.getState();
    before.setVisibleStorey(S0);
    // Idempotent set must not publish: a re-render loop in the 3D controls
    // would otherwise cost a React commit per frame.
    expect(useThreeStore.getState()).toBe(before);

    useThreeStore.getState().toggleVisibleStorey(S0);
    expect(useThreeStore.getState().visibleStoreyId).toBeNull();
    useThreeStore.getState().toggleVisibleStorey(S1);
    expect(useThreeStore.getState().visibleStoreyId).toBe(S1);
  });

  it('resets to "all" when the filtered storey stops existing (delete/undo)', () => {
    useModelStore.setState({
      doc: docWith((d) => {
        d.house.storeys = [
          { id: S0, name: 'Ground Floor', heightMm: 3000 },
          { id: S1, name: 'First Floor', heightMm: 3000 },
        ];
      }),
    });
    useThreeStore.getState().setVisibleStorey(S1);

    useModelStore.setState({
      doc: docWith((d) => {
        d.house.storeys = [{ id: S0, name: 'Ground Floor', heightMm: 3000 }];
      }),
    });
    expect(
      useThreeStore.getState().visibleStoreyId,
      'a deleted storey must not leave the 3D view pinned to nothing',
    ).toBeNull();
  });
});

describe('the applied-facade mirror', () => {
  it('follows the model store: apply, edit (component count), clear', () => {
    useModelStore.setState({
      doc: docWith((d) => {
        d.house.facade = {
          kitId: 'contemporary',
          seed: 7,
          colorwayId: 'mono-wood',
          components: [{ id: COMP }],
        };
      }),
    });
    expect(useThreeStore.getState().appliedFacade).toEqual({
      kitId: 'contemporary',
      seed: 7,
      colorwayId: 'mono-wood',
      componentCount: 1,
    });

    useModelStore.setState({ doc: emptyProjectDoc() });
    expect(useThreeStore.getState().appliedFacade).toEqual({
      kitId: null,
      seed: 0,
      colorwayId: null,
      componentCount: 0,
    });
  });

  it('appliedFacadeOf reads exactly the fold-guaranteed fields', () => {
    expect(
      appliedFacadeOf({
        facade: { kitId: 'modern-minimal', seed: 42, colorwayId: null, components: [1, 2, 3] },
      }),
    ).toEqual({ kitId: 'modern-minimal', seed: 42, colorwayId: null, componentCount: 3 });
  });

  it('an unrelated model write does not publish a new three state', () => {
    const before = useThreeStore.getState();
    // Same doc identity → the subscription's identity guard exits early.
    useModelStore.setState({ headIdx: 99 });
    expect(useThreeStore.getState()).toBe(before);
  });
});

describe('rebuild telemetry', () => {
  it('counts only rebuilds that re-meshed something — the sun-scrub probe', () => {
    const note = useThreeStore.getState().noteRebuild;
    note({ ms: 12.5, rebuiltGroups: ['storey:a'], totalGroups: 3, holesApplied: true });
    note({ ms: 0.2, rebuiltGroups: [], totalGroups: 3, holesApplied: true });
    note({ ms: 0.1, rebuiltGroups: [], totalGroups: 3, holesApplied: true });

    const last = useThreeStore.getState().lastRebuild;
    expect(last?.rebuildCount, 'no-op recomputes must not count as rebuilds').toBe(1);
    expect(last?.ms).toBeCloseTo(0.1);
  });

  it('engine status dedupes and keeps the unavailable reason', () => {
    const s = useThreeStore.getState();
    s.noteEngineStatus('loading');
    const afterFirst = useThreeStore.getState();
    afterFirst.noteEngineStatus('loading');
    expect(useThreeStore.getState()).toBe(afterFirst);

    useThreeStore.getState().noteEngineStatus('unavailable', 'WASM fetch failed');
    expect(useThreeStore.getState().engineStatus).toBe('unavailable');
    expect(useThreeStore.getState().engineDetail).toBe('WASM fetch failed');
  });
});
