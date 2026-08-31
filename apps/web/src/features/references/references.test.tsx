/**
 * The inspiration board, driven by real clicks and keystrokes on a real DOM.
 *
 * Same harness as `AssetBrowser.test.tsx` — `createRoot` into jsdom, native
 * value setters, React's own event system — because this workspace has no
 * testing library and a hand-rolled renderer would be testing my own harness.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THIS FILE IS ACTUALLY GUARDING
 * ════════════════════════════════════════════════════════════════════════════
 * Not "the board renders". A board that renders, uploads and looks complete
 * while contributing nothing to a render is CLAUDE.md's fourth bug class
 * exactly — the furniture layer that tagged its meshes, documented itself as
 * integrated, and never called the registry. So the cases below assert the
 * CALLS: that editing "what to take from it" reaches the API with the text the
 * architect typed, that the launcher asks for a review of the preset actually
 * selected, and that changing the preset re-asks rather than showing a stale
 * verdict about a different view.
 *
 * NEGATIVE CONTROLS (each applied, `vitest run src/features/references/` run,
 * the failure observed, the change reverted):
 *
 *   A. `ReferenceBoard.tsx` — drop the `onBlur` commit on the "what to take"
 *      textarea, keeping the local `onChange`. The field still types, still
 *      shows the text, still looks saved.
 *        Tests  1 failed | 16 passed
 *
 *   B. `useReferenceReview.ts` — `setReview(null)` on success, so the hook
 *      always answers "nothing".
 *        Tests  2 failed | 15 passed
 *
 *   C. `useReferenceReview.ts` — drop `preset` from the effect's dependency
 *      array, so the first verdict sticks. This is the subtle one: every
 *      review call still happens, the panel still shows real conflicts, and
 *      the answer is simply about the wrong view.
 *        Tests  1 failed | 16 passed
 *
 *   D. `store.ts` `annotate` — stop clearing `review`. A stale verdict then
 *      claims a board that no longer exists.
 *        Tests  1 failed | 16 passed
 */

import type { ReactElement } from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, REFERENCE_SCOPES, type ProjectReference, type ReferenceReview } from '../../lib/api';
import { ReferenceBoard } from './ReferenceBoard';
import { useReferenceStore } from './store';
import { useReferenceReview } from './useReferenceReview';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const PROJECT = 'project-1';
const PRESETS = [
  { id: 'elevation-north-morning', label: 'North elevation, morning' },
  { id: 'interior-living', label: 'Living room' },
];

function reference(overrides: Partial<ProjectReference> = {}): ProjectReference {
  return {
    id: 'ref-1',
    projectId: PROJECT,
    label: "Client's verandah photo",
    scope: 'facade',
    why: '',
    ignore: '',
    intent: 'guide',
    position: 0,
    filename: 'verandah.png',
    widthPx: 640,
    heightPx: 480,
    imageUrl: 'https://storage.invalid/ref-1.png',
    createdAt: '2026-08-31T00:00:00Z',
    ...overrides,
  };
}

function review(overrides: Partial<ReferenceReview> = {}): ReferenceReview {
  return {
    projectId: PROJECT,
    preset: 'elevation-north-morning',
    applies: [],
    notInView: [],
    conflicts: [],
    positive: '',
    negative: '',
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;

function mount(element: ReactElement): void {
  act(() => {
    root.render(element);
  });
}

/** Let the mounted effects' promises settle. */
async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function typeInto(element: HTMLTextAreaElement | HTMLInputElement, value: string): void {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  act(() => {
    setter?.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/**
 * Blur the way React hears it.
 *
 * React 18 delegates at the root and listens for the BUBBLING `focusout`, not
 * the non-bubbling `blur`, so dispatching a `blur` event fires nothing and every
 * onBlur assertion below would silently pass against a component with no handler
 * at all — the can't-fail check CLAUDE.md warns about.
 */
function blur(element: Element): void {
  act(() => {
    element.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
  });
}

function choose(select: HTMLSelectElement, value: string): void {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(select, value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

function click(element: Element): void {
  act(() => {
    (element as HTMLElement).click();
  });
}

function byLabel<T extends Element>(label: string): T {
  const found = container.querySelector<T>(`[aria-label="${label}"]`);
  if (found === null) throw new Error(`no element labelled "${label}"`);
  return found;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  useReferenceStore.setState({
    byProject: {},
    loading: false,
    error: null,
    review: null,
    reviewing: false,
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  try {
    act(() => root.unmount());
  } catch {
    // already unmounted
  }
  container.remove();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// The four answers reach the server
// ---------------------------------------------------------------------------

describe('annotating a reference', () => {
  it('typing what to take from a reference saves it', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference()]);
    const annotate = vi
      .spyOn(api.references, 'annotate')
      .mockResolvedValue(reference({ why: 'the deep shaded verandah' }));

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    const [why] = container.querySelectorAll<HTMLTextAreaElement>('textarea');
    if (why === undefined) throw new Error('no "what to take" field on the card');
    typeInto(why, 'the deep shaded verandah');
    blur(why);
    await settle();

    expect(annotate).toHaveBeenCalledWith(PROJECT, 'ref-1', {
      why: 'the deep shaded verandah',
    });
  });

  it('an annotation is sent as a partial patch, never the whole row', async () => {
    // The server's PATCH is partial by design so an architect can come back to a
    // card. Sending the whole row would make every edit overwrite fields another
    // person may have just changed.
    vi.spyOn(api.references, 'list').mockResolvedValue([reference({ why: 'kept' })]);
    const annotate = vi
      .spyOn(api.references, 'annotate')
      .mockResolvedValue(reference({ why: 'kept', ignore: 'the glass balustrade' }));

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    const fields = container.querySelectorAll<HTMLTextAreaElement>('textarea');
    const ignore = fields[1];
    if (ignore === undefined) throw new Error('no "what to leave out" field on the card');
    typeInto(ignore, 'the glass balustrade');
    blur(ignore);
    await settle();

    expect(annotate).toHaveBeenCalledWith(PROJECT, 'ref-1', {
      ignore: 'the glass balustrade',
    });
    expect(annotate.mock.calls[0]?.[2]).not.toHaveProperty('why');
  });

  it('choosing where a reference applies saves the scope', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference()]);
    const annotate = vi
      .spyOn(api.references, 'annotate')
      .mockResolvedValue(reference({ scope: 'kitchen' }));

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    choose(byLabel<HTMLSelectElement>("Where Client's verandah photo applies"), 'kitchen');
    await settle();

    expect(annotate).toHaveBeenCalledWith(PROJECT, 'ref-1', { scope: 'kitchen' });
  });

  it('a blank name snaps back rather than sending a 422', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference()]);
    const annotate = vi.spyOn(api.references, 'annotate');

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    const name = byLabel<HTMLInputElement>('Reference name');
    typeInto(name, '   ');
    blur(name);
    await settle();

    expect(annotate).not.toHaveBeenCalled();
    expect(name.value).toBe("Client's verandah photo");
  });

  it('every scope the server accepts is offered', async () => {
    // A scope the UI cannot set is a scope no architect will ever use; a scope
    // the UI offers and the server refuses is a 422 they cannot act on. The
    // Python side keeps its own enum equal to the render side's
    // (test_reference_vocabulary.py); this is the third corner of that triangle.
    vi.spyOn(api.references, 'list').mockResolvedValue([reference()]);
    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    const select = byLabel<HTMLSelectElement>("Where Client's verandah photo applies");
    const offered = [...select.options].map((option) => option.value);
    expect(offered).toEqual([...REFERENCE_SCOPES]);
  });
});

// ---------------------------------------------------------------------------
// The board tells the architect what is not answered
// ---------------------------------------------------------------------------

describe('what the board says about itself', () => {
  it('counts the references nobody has described yet', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([
      reference({ id: 'a' }),
      reference({ id: 'b', why: 'the deep verandah' }),
      reference({ id: 'c' }),
    ]);

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    expect(container.textContent).toContain('2 not yet described');
  });

  it('says nothing about descriptions when every reference has one', async () => {
    // NEGATIVE CONTROL for the case above: without it, a badge hard-coded to
    // show would pass that assertion.
    vi.spyOn(api.references, 'list').mockResolvedValue([
      reference({ id: 'b', why: 'the deep verandah' }),
    ]);

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    expect(container.textContent).not.toContain('not yet described');
  });
});

// ---------------------------------------------------------------------------
// The review: the questions asked before a render
// ---------------------------------------------------------------------------

describe('checking the board before a render', () => {
  it('asks the server about the style the architect picked', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference({ why: 'the verandah' })]);
    const ask = vi.spyOn(api.references, 'review').mockResolvedValue(review());

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    choose(
      byLabel<HTMLSelectElement>('Render style to check the board against'),
      'interior-living',
    );
    const button = [...container.querySelectorAll('button')].find(
      (b) => b.textContent?.includes('Check before rendering') === true,
    );
    if (button === undefined) throw new Error('no "check before rendering" button');
    click(button);
    await settle();

    expect(ask).toHaveBeenCalledWith(PROJECT, 'interior-living');
  });

  it('shows each question with what happens if the architect does nothing', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference({ why: 'the verandah' })]);
    vi.spyOn(api.references, 'review').mockResolvedValue(
      review({
        conflicts: [
          {
            kind: 'competing',
            referenceIds: ['ref-1', 'ref-2'],
            question: 'A, B are all set to match closely for the facade. Which one should win?',
            default: 'The first one is followed and the rest are treated as a guide.',
          },
        ],
      }),
    );

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();
    const button = [...container.querySelectorAll('button')].find(
      (b) => b.textContent?.includes('Check before rendering') === true,
    );
    click(button as Element);
    await settle();

    const panel = container.querySelector('[data-testid="reference-review"]');
    expect(panel?.textContent).toContain('Which one should win?');
    // A question with an unknown default is one people dismiss.
    expect(panel?.textContent).toContain('If you do nothing:');
    expect(panel?.textContent).toContain('treated as a guide');
  });

  it('names the references it will not use in this view', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference({ why: 'walnut fronts' })]);
    vi.spyOn(api.references, 'review').mockResolvedValue(
      review({ notInView: [reference({ label: 'Hotel bathroom', why: 'the ribbed screen' })] }),
    );

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();
    click(
      [...container.querySelectorAll('button')].find(
        (b) => b.textContent?.includes('Check before rendering') === true,
      ) as Element,
    );
    await settle();

    // Silently dropping it is how an architect concludes the board does nothing.
    expect(container.textContent).toContain('Not used in this view: Hotel bathroom');
  });

  it('shows the exact words the render will be told', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference({ why: 'the verandah' })]);
    vi.spyOn(api.references, 'review').mockResolvedValue(
      review({
        applies: [reference({ why: 'the verandah' })],
        positive: 'closely following the deep shaded verandah',
        negative: 'the glass balustrade',
      }),
    );

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();
    click(
      [...container.querySelectorAll('button')].find(
        (b) => b.textContent?.includes('Check before rendering') === true,
      ) as Element,
    );
    await settle();

    // §11's honesty claim: the instruction written and the instruction sent are
    // visibly the same text.
    expect(container.textContent).toContain('closely following the deep shaded verandah');
    expect(container.textContent).toContain('the glass balustrade');
  });
});

// ---------------------------------------------------------------------------
// The store's own rules
// ---------------------------------------------------------------------------

describe('the board store', () => {
  it('drops a review as soon as the board changes under it', async () => {
    useReferenceStore.setState({ review: review({ positive: 'stale' }) });
    vi.spyOn(api.references, 'annotate').mockResolvedValue(reference({ why: 'new' }));

    await useReferenceStore.getState().annotate(PROJECT, 'ref-1', { why: 'new' });

    // A verdict about a board that no longer exists is worse than no verdict:
    // it is confidently about the wrong thing.
    expect(useReferenceStore.getState().review).toBeNull();
  });

  it('keeps a failed write out of the board and says what went wrong', async () => {
    useReferenceStore.setState({ byProject: { [PROJECT]: [reference()] } });
    vi.spyOn(api.references, 'annotate').mockRejectedValue(new Error('scope must be one of …'));

    await useReferenceStore.getState().annotate(PROJECT, 'ref-1', { why: 'never saved' });

    const [only] = useReferenceStore.getState().byProject[PROJECT] ?? [];
    // No optimistic local edit: an annotation the server refused must not sit on
    // screen looking saved while the render follows something else.
    expect(only?.why).toBe('');
    expect(useReferenceStore.getState().error).toContain('scope must be one of');
  });
});

// ---------------------------------------------------------------------------
// The launcher's hook — the questions asked at the moment a render starts
// ---------------------------------------------------------------------------

/**
 * A probe rather than the real `RenderLauncher`, which needs a live R3F scene.
 * The hook IS the integration under test: the launcher's only job is to render
 * what this returns, and a hook that answers about the wrong preset would put a
 * confident, wrong question in front of an architect about to make a client
 * image.
 */
function ReviewProbe({ preset }: { preset: string | null }): JSX.Element {
  const review = useReferenceReview(PROJECT, preset);
  return <p data-testid="probe">{review === null ? 'none' : review.preset}</p>;
}

function probeText(): string {
  return container.querySelector('[data-testid="probe"]')?.textContent ?? '';
}

describe('the review the launcher asks for', () => {
  it('asks about the style that is actually selected', async () => {
    const ask = vi
      .spyOn(api.references, 'review')
      .mockImplementation((_projectId, preset) => Promise.resolve(review({ preset })));

    mount(<ReviewProbe preset="interior-living" />);
    await settle();

    expect(ask).toHaveBeenCalledWith(PROJECT, 'interior-living', expect.anything());
    expect(probeText()).toBe('interior-living');
  });

  it('re-asks when the architect changes the style', async () => {
    // "What applies" is a question about a specific view. A verdict left over
    // from another preset answers the wrong question with the same confidence —
    // and nothing on screen would say so.
    const ask = vi
      .spyOn(api.references, 'review')
      .mockImplementation((_projectId, preset) => Promise.resolve(review({ preset })));

    mount(<ReviewProbe preset="elevation-north-morning" />);
    await settle();
    expect(probeText()).toBe('elevation-north-morning');

    mount(<ReviewProbe preset="interior-living" />);
    await settle();

    expect(ask).toHaveBeenCalledTimes(2);
    expect(probeText()).toBe('interior-living');
  });

  it('says nothing at all when the review cannot be fetched', async () => {
    // Including the honest 503 when the render package is not loaded. The board
    // is additive: it must never block a render or show a scary banner over a
    // feature this project may not even be using.
    vi.spyOn(api.references, 'review').mockRejectedValue(new Error('service unavailable'));

    mount(<ReviewProbe preset="elevation-north-morning" />);
    await settle();

    expect(probeText()).toBe('none');
  });

  it('asks nothing at all before a style is chosen', async () => {
    const ask = vi.spyOn(api.references, 'review').mockResolvedValue(review());

    mount(<ReviewProbe preset={null} />);
    await settle();

    expect(ask).not.toHaveBeenCalled();
    expect(probeText()).toBe('none');
  });
});
