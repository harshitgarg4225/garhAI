/**
 * "Check before rendering" must be checking what the architect just wrote.
 *
 * Every answer on a card commits on BLUR. Clicking the review button blurs the box
 * they were typing in, so the PATCH and the review POST leave the browser in that
 * order and then race on the server. Lose the race and the review reads the row as
 * it was a moment ago and asks "What should this picture contribute? If you do
 * nothing: it is skipped" — about the sentence just finished.
 *
 * That is the worst answer this feature can give. It is about whether the product
 * heard you, it lands exactly when you are being careful, and clicking again makes
 * it go away, which teaches people the check is noise.
 *
 * The control below is what keeps the fix from becoming "the review waits for
 * everything, forever": a write that starts AFTER the review was asked for is not
 * one the architect was waiting on.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ProjectReference, ReferenceReview } from '../../lib/api';
import { api } from '../../lib/api';
import { useReferenceStore } from './store';

const PROJECT = 'proj_1';

function reference(over: Partial<ProjectReference> = {}): ProjectReference {
  return {
    id: 'ref_1',
    label: 'kitchen-tiles.jpg',
    scope: 'kitchen',
    intent: 'guide',
    why: '',
    ignore: '',
    imageUrl: 'https://example.invalid/a.jpg',
    widthPx: 900,
    heightPx: 600,
    ...over,
  } as ProjectReference;
}

function emptyReview(): ReferenceReview {
  return {
    projectId: PROJECT,
    preset: 'exterior-street-day',
    applies: [],
    conflicts: [],
    notInView: [],
    positive: '',
    negative: '',
  } as ReferenceReview;
}

/** A promise plus its resolver, so a test can decide when a call comes back. */
function gate(): { wait: Promise<void>; open: () => void } {
  let open = (): void => {
    /* replaced synchronously by the executor below */
  };
  const wait = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { wait, open };
}

beforeEach(() => {
  vi.restoreAllMocks();
  useReferenceStore.setState({
    byProject: { [PROJECT]: [reference()] },
    loading: false,
    error: null,
    review: null,
    reviewing: false,
  });
});

describe('the review and the answer still in flight', () => {
  it('waits for a pending answer before asking for the verdict', async () => {
    const order: string[] = [];
    const annotateGate = gate();

    vi.spyOn(api.references, 'annotate').mockImplementation(async () => {
      await annotateGate.wait;
      order.push('annotate');
      return reference({ why: 'the walnut cabinet fronts' });
    });
    vi.spyOn(api.references, 'review').mockImplementation(() => {
      order.push('review');
      return Promise.resolve(emptyReview());
    });

    const store = useReferenceStore.getState();
    // The blur commit, then the click — the order a real architect produces.
    const writing = store.annotate(PROJECT, 'ref_1', { why: 'the walnut cabinet fronts' });
    const reviewing = store.review_(PROJECT, 'exterior-street-day');

    // Nothing has been asked of the review endpoint yet.
    await Promise.resolve();
    expect(order, 'the verdict was requested while the answer was still in flight').toEqual([]);

    annotateGate.open();
    await Promise.all([writing, reviewing]);

    expect(order).toEqual(['annotate', 'review']);
  });

  it('NEGATIVE CONTROL: it does not wait for a write that started afterwards', async () => {
    // Awaiting the live set rather than a snapshot would mean a board being edited
    // in another tab could hold the button down indefinitely.
    vi.spyOn(api.references, 'review').mockResolvedValue(emptyReview());
    const lateGate = gate();
    vi.spyOn(api.references, 'annotate').mockImplementation(() =>
      lateGate.wait.then(() => reference({ why: 'late' })),
    );

    const store = useReferenceStore.getState();
    const reviewing = store.review_(PROJECT, 'exterior-street-day');
    const late = store.annotate(PROJECT, 'ref_1', { why: 'late' });

    await reviewing;
    expect(useReferenceStore.getState().reviewing).toBe(false);

    lateGate.open();
    await late;
  });

  it('a failed answer does not strand the review', async () => {
    // `Promise.allSettled`, not `Promise.all`: a PATCH that 500s must still let
    // the architect ask for a verdict, with the error already on screen.
    vi.spyOn(api.references, 'annotate').mockRejectedValue(new Error('nope'));
    const ask = vi.spyOn(api.references, 'review').mockResolvedValue(emptyReview());

    const store = useReferenceStore.getState();
    const writing = store.annotate(PROJECT, 'ref_1', { why: 'x' });
    await store.review_(PROJECT, 'exterior-street-day');
    await writing;

    expect(ask).toHaveBeenCalledTimes(1);
    expect(useReferenceStore.getState().error).toBe('nope');
  });
});
