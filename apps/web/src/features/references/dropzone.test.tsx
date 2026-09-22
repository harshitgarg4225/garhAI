/**
 * Dropping the pictures a client sent, because that is how they arrive.
 *
 * Photos come on WhatsApp and in email. The architect saves them to a folder and
 * then — before this — had to find a button, open a file picker, and navigate back
 * to the folder they were already looking at. The board had a `multiple` file input
 * and nothing else; the store's own comment even reasoned about "one architect's
 * drag-and-drop" for a drop target that did not exist.
 *
 * Three things here are the ones that go wrong in every hand-rolled drop zone, and
 * each has a case:
 *
 *   1. `dragover` without `preventDefault` — the browser navigates to the dropped
 *      file and the project vanishes. The drop handler never even runs.
 *   2. `dragleave` fires when the pointer crosses a CHILD. A board is all children,
 *      so a boolean flickers the highlight off exactly while you are deciding where
 *      to let go. Hence the depth count.
 *   3. A non-image lands and nothing happens, so it gets dropped three more times.
 *
 * Same harness as `references.test.tsx`: `createRoot` into jsdom and real events.
 */

import type { ReactElement } from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api, type ProjectReference } from '../../lib/api';
import { ReferenceBoard } from './ReferenceBoard';
import { useReferenceStore } from './store';

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const PROJECT = 'project-1';
const PRESETS = [{ id: 'exterior-street-day', label: 'Street view, daylight' }];

let container: HTMLDivElement;
let root: Root;

function reference(over: Partial<ProjectReference> = {}): ProjectReference {
  return {
    id: 'ref-1',
    projectId: PROJECT,
    label: 'kitchen.jpg',
    filename: 'kitchen.jpg',
    scope: 'kitchen',
    intent: 'guide',
    why: '',
    ignore: '',
    position: 0,
    widthPx: 900,
    heightPx: 600,
    imageUrl: 'https://example.invalid/a.jpg',
    ...over,
  } as ProjectReference;
}

function mount(element: ReactElement): void {
  act(() => root.render(element));
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function png(name: string, type = 'image/png'): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type });
}

/** A DataTransfer jsdom will carry on a synthetic drag event. */
function transfer(files: readonly File[]): DataTransfer {
  return {
    files: files as unknown as FileList,
    items: files as unknown as DataTransferItemList,
    types: files.length > 0 ? ['Files'] : [],
  } as unknown as DataTransfer;
}

function fire(node: Element, type: string, files: readonly File[]): boolean {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: transfer(files) });
  let dispatched = false;
  act(() => {
    dispatched = node.dispatchEvent(event);
  });
  // `dispatchEvent` returns false when something called preventDefault.
  return dispatched;
}

function zone(): HTMLElement {
  const el = container.querySelector('[data-testid="reference-dropzone"]');
  if (!(el instanceof HTMLElement)) throw new Error('no drop zone on the board');
  return el;
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
  vi.spyOn(api.references, 'list').mockResolvedValue([]);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe('dropping pictures onto the board', () => {
  it('uploads every image that was dropped', async () => {
    const add = vi
      .spyOn(api.references, 'add')
      .mockImplementation(({ file }) =>
        Promise.resolve(reference({ id: (file as File).name, label: (file as File).name })),
      );

    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    fire(zone(), 'drop', [png('kitchen.jpg', 'image/jpeg'), png('facade.png')]);
    await settle();

    expect(add).toHaveBeenCalledTimes(2);
    expect((add.mock.calls[0]?.[0].file as File).name).toBe('kitchen.jpg');
    expect((add.mock.calls[1]?.[0].file as File).name).toBe('facade.png');
  });

  it('cancels dragover, or the browser navigates away from the project', async () => {
    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();
    const notCancelled = fire(zone(), 'dragover', [png('a.png')]);
    expect(
      notCancelled,
      'dragover was left to the browser: dropping a file would open it as a page and ' +
        'the architect would lose the project they were in',
    ).toBe(false);
  });

  it('keeps the highlight on while the pointer crosses the cards', async () => {
    vi.spyOn(api.references, 'list').mockResolvedValue([reference(), reference({ id: 'ref-2' })]);
    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    // Entering the board, then a card inside it: two enters, one leave.
    fire(zone(), 'dragenter', [png('a.png')]);
    expect(zone().getAttribute('data-dropping')).toBe('true');

    const card = container.querySelector('li');
    if (card === null) throw new Error('no card to drag across');
    fire(card, 'dragenter', [png('a.png')]);
    fire(card, 'dragleave', [png('a.png')]);
    expect(
      zone().getAttribute('data-dropping'),
      'the board stopped looking like a target while the pointer was still over it',
    ).toBe('true');

    fire(zone(), 'dragleave', [png('a.png')]);
    expect(zone().getAttribute('data-dropping')).toBe(null);
  });

  it('NEGATIVE CONTROL: dragging text over the board does not light it up', async () => {
    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();
    // No files on the transfer — a text selection being dragged across the page.
    fire(zone(), 'dragenter', []);
    expect(zone().getAttribute('data-dropping')).toBe(null);
  });

  it('names what it would not take, instead of ignoring it silently', async () => {
    const add = vi.spyOn(api.references, 'add').mockResolvedValue(reference());
    mount(<ReferenceBoard projectId={PROJECT} presets={PRESETS} />);
    await settle();

    fire(zone(), 'drop', [
      png('magazine-page.pdf', 'application/pdf'),
      png('kitchen.jpg', 'image/jpeg'),
    ]);
    await settle();

    expect(add, 'the PDF was uploaded anyway').toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('magazine-page.pdf');
    expect(container.textContent).toMatch(/not PNG or JPEG/i);
  });
});
