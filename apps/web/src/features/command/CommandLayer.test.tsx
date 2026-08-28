/**
 * The whole feature, driven through a real DOM with real keyboard events.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS TEST IS AT THIS LEVEL AND NOT LOWER
 * ════════════════════════════════════════════════════════════════════════════
 * `registry.test.ts` proves the registry accepts and refuses the right things.
 * That is not the same as proving the app is wired to it — and "wired to it" is
 * precisely what nobody could prove about the furniture layer, which tagged its
 * meshes, documented itself as integrated, and never called the registry.
 *
 * So every assertion below starts from `<CommandLayer />` being mounted and a
 * `KeyboardEvent` being dispatched at a real node, and ends at store state that
 * something else in the app reads. If registration silently stopped happening,
 * if the listener were never attached, if the palette read a different list
 * than the key layer — each of those goes red here and nowhere else.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE GUARD, AND ITS NEGATIVE CONTROL
 * ════════════════════════════════════════════════════════════════════════════
 * "Does not fire while you are typing" is the assertion most easily faked: a
 * test that dispatches an event the layer would have ignored anyway passes with
 * the guard deleted. Every guard case below is therefore paired with the SAME
 * event dispatched at a non-typing node, which must open the palette. If the
 * pair ever both come back "closed", the dispatch is broken and the guard test
 * proves nothing.
 */

import { act, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useUiStore } from '../../stores/ui';
import { CommandLayer } from './CommandLayer';
import { CommandRegistry } from './registry';
import { useCommandUiStore } from './store';

declare global {
  // React 18 reads this to decide whether `act` warnings apply.
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let registry: CommandRegistry;

function mount(element: ReactElement): void {
  act(() => {
    root.render(element);
  });
}

/** Mount the layer the way `App.tsx` will, on a non-Mac keyboard. */
function mountLayer(props: { ownHelpKey?: boolean } = {}): void {
  mount(<CommandLayer registry={registry} mac={false} {...props} />);
}

/**
 * Press a key at `target`, as a real bubbling `KeyboardEvent`.
 *
 * `bubbles: true` matters: the layer listens on `document`, and an event that
 * does not bubble would be ignored for the wrong reason — which would make the
 * typing-guard tests pass for free.
 */
function press(target: EventTarget, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

/** Ctrl-K, the `mod+k` of a non-Mac keyboard. */
function pressModK(target: EventTarget): KeyboardEvent {
  return press(target, 'k', { ctrlKey: true });
}

function palette(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('[aria-label="Command palette"]');
}

function cheatsheet(): HTMLElement | null {
  return document.body.querySelector<HTMLElement>('[aria-label="Keyboard shortcuts"]');
}

function rows(): HTMLElement[] {
  return [...document.body.querySelectorAll<HTMLElement>('[role="option"]')];
}

function rowTitles(): string[] {
  return rows().map((row) => row.textContent ?? '');
}

function searchInput(): HTMLInputElement {
  const input = document.body.querySelector<HTMLInputElement>('input[role="combobox"]');
  expect(input, 'the palette is not open').not.toBeNull();
  return input as HTMLInputElement;
}

/**
 * Type into React's controlled input the way a keyboard would.
 *
 * Assigning `input.value` alone does not produce an `onChange`: React keeps a
 * `_valueTracker` on the node recording what it last rendered, and an
 * assignment updates that tracker on the way past, so React compares the two
 * and concludes nothing changed. Dropping the tracker first makes React treat
 * the next `input` event as a real edit — which is what a keystroke is.
 */
function typeQuery(text: string): void {
  const input = searchInput();
  act(() => {
    Reflect.deleteProperty(input, '_valueTracker');
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

/** A node the guard has no opinion about — the negative control's target. */
function plainNode(): HTMLDivElement {
  const div = document.createElement('div');
  document.body.append(div);
  return div;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  registry = new CommandRegistry();
  useCommandUiStore.setState({
    paletteOpen: false,
    cheatsheetOpen: false,
    query: '',
    highlightedId: null,
  });
  useUiStore.setState({
    activeTool: 'select',
    viewMode: '2d',
    keyboardEnabled: true,
    canvasLayers: {
      grid: true,
      dimensions: true,
      roomTags: true,
      furniture: true,
      compliance: true,
    },
  });
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.replaceChildren();
});

describe('the layer is actually registered', () => {
  it('populates the registry on mount and empties it on unmount', () => {
    expect(registry.size).toBe(0);
    mountLayer();
    // The bug this catches: a feature that documents itself as integrated and
    // never calls the registry. Nothing else in the build would notice.
    expect(registry.size).toBeGreaterThan(20);
    expect(registry.has('tool.wall')).toBe(true);
    expect(registry.has('palette.open')).toBe(true);

    act(() => root.unmount());
    expect(registry.size).toBe(0);
    root = createRoot(container); // afterEach unmounts again; give it something
  });
});

describe('the palette opens, filters and runs', () => {
  it('opens on mod+K and closes on Escape', () => {
    mountLayer();
    expect(palette()).toBeNull();

    const opened = pressModK(document);
    expect(palette()).not.toBeNull();
    // A handled shortcut must not also reach the browser, whose own Ctrl-K
    // focuses the address bar.
    expect(opened.defaultPrevented).toBe(true);

    press(searchInput(), 'Escape');
    expect(palette()).toBeNull();
  });

  it('lists everything runnable at rest, and filters as you type', () => {
    mountLayer();
    pressModK(document);
    const atRest = rowTitles().length;
    expect(atRest).toBeGreaterThan(10);

    typeQuery('wall');
    const filtered = rowTitles();
    expect(filtered.length).toBeLessThan(atRest);
    expect(filtered.join('|')).toContain('Draw walls');
    // The half that makes the assertion above mean something: the commands
    // that do NOT match are gone, not merely re-sorted.
    expect(filtered.join('|')).not.toContain('Place a door');
  });

  it('says so when nothing matches, instead of showing an empty box', () => {
    mountLayer();
    pressModK(document);
    typeQuery('zzzzqqq');
    expect(rows()).toHaveLength(0);
    expect(palette()?.textContent).toContain('Nothing matches');
  });

  it('runs the highlighted row on Enter and changes real app state', () => {
    mountLayer();
    pressModK(document);
    typeQuery('draw wall');
    expect(rowTitles()[0]).toContain('Draw walls');

    press(searchInput(), 'Enter');

    // Through the registry, into the store the tool rail reads. Not a spy.
    expect(useUiStore.getState().activeTool).toBe('wall');
    expect(palette()).toBeNull();
  });

  it('walks the list with the arrow keys, in the order it is drawn', () => {
    mountLayer();
    pressModK(document);
    typeQuery('show');
    const titles = rowTitles();
    expect(titles.length).toBeGreaterThan(1);

    const input = searchInput();
    press(input, 'ArrowDown');
    const selected = rows().filter((row) => row.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1);
    expect(selected[0]?.textContent).toBe(titles[1]);

    // …and wraps rather than sticking at the ends.
    press(input, 'ArrowUp');
    press(input, 'ArrowUp');
    const wrapped = rows().filter((row) => row.getAttribute('aria-selected') === 'true');
    expect(wrapped[0]?.textContent).toBe(titles[titles.length - 1]);
  });

  it('runs a row on click', () => {
    mountLayer();
    pressModK(document);
    typeQuery('hide the grid');
    const before = useUiStore.getState().canvasLayers.grid;

    const row = rows()[0];
    expect(row?.textContent).toContain('grid');
    act(() => row?.click());

    expect(useUiStore.getState().canvasLayers.grid).toBe(!before);
    expect(palette()).toBeNull();
  });
});

describe('the typing guard', () => {
  /**
   * Each case dispatches the SAME keystroke twice: once at a node the guard
   * must protect, once at a plain div it must not. Without the second half,
   * a broken dispatch would make the first half pass on its own.
   */
  function guarded(makeTarget: () => HTMLElement, label: string): void {
    it(`does not fire inside ${label}`, () => {
      mountLayer();
      const target = makeTarget();
      pressModK(target);
      expect(palette(), `mod+K fired while the user was typing in ${label}`).toBeNull();

      // NEGATIVE CONTROL — the same event, at a node the guard ignores.
      pressModK(plainNode());
      expect(
        palette(),
        'the dispatch itself is broken; the assertion above is empty',
      ).not.toBeNull();
    });
  }

  guarded(() => {
    const input = document.createElement('input');
    document.body.append(input);
    return input;
  }, 'a text input');

  guarded(() => {
    const input = document.createElement('input');
    input.type = 'number';
    document.body.append(input);
    return input;
    // A dimension field: typing 2400 must not also open the palette.
  }, 'a number input');

  guarded(() => {
    const area = document.createElement('textarea');
    document.body.append(area);
    return area;
  }, 'a textarea');

  guarded(() => {
    const node = document.createElement('div');
    node.setAttribute('contenteditable', 'true');
    document.body.append(node);
    // jsdom does not derive isContentEditable from the attribute.
    Object.defineProperty(node, 'isContentEditable', { value: true });
    return node;
  }, 'a contenteditable region');

  guarded(() => {
    const wrapper = document.createElement('div');
    wrapper.setAttribute('data-garh-keys', 'off');
    const inner = document.createElement('button');
    wrapper.append(inner);
    document.body.append(wrapper);
    return inner;
  }, 'a region that opted out with data-garh-keys');

  it('still fires over a checkbox, which is pressed and not typed into', () => {
    // The guard must not be a blanket "any form control". lib/keymap.ts is
    // explicit about this and the layer inherits it, so it is asserted here.
    mountLayer();
    const box = document.createElement('input');
    box.type = 'checkbox';
    document.body.append(box);
    pressModK(box);
    expect(palette()).not.toBeNull();
  });

  it('ignores auto-repeat, so a held key does not flap the palette', () => {
    mountLayer();
    press(document, 'k', { ctrlKey: true, repeat: true });
    expect(palette()).toBeNull();
    pressModK(document);
    expect(palette()).not.toBeNull();
  });

  it('goes quiet while a focus-trapped dialog owns the keyboard', () => {
    mountLayer();
    act(() => useUiStore.getState().setKeyboardEnabled(false));
    pressModK(document);
    expect(palette()).toBeNull();

    act(() => useUiStore.getState().setKeyboardEnabled(true));
    pressModK(document);
    expect(palette()).not.toBeNull();
  });
});

describe('a disabled command cannot be invoked', () => {
  it('shows it, refuses Enter, and accepts it once the reason goes away', () => {
    mountLayer();
    // In the 3D view the eight tools are unavailable — W walks the camera.
    act(() => useUiStore.setState({ viewMode: '3d', activeTool: 'select' }));

    pressModK(document);
    typeQuery('draw wall');
    const row = rows()[0];
    expect(row?.textContent).toContain('Draw walls');
    // Shown, not hidden: a palette that silently drops what you searched for is
    // indistinguishable from an app that cannot do it at all.
    expect(row?.getAttribute('aria-disabled')).toBe('true');

    press(searchInput(), 'Enter');
    expect(useUiStore.getState().activeTool).toBe('select');
    expect(palette(), 'the palette closed as though something had happened').not.toBeNull();

    act(() => rows()[0]?.click());
    expect(useUiStore.getState().activeTool).toBe('select');

    // NEGATIVE CONTROL: the identical Enter, with the command available. If
    // this did not run, every assertion above would hold for a palette that
    // could never invoke anything.
    //
    // Note what is deliberately NOT done first: nothing re-renders the palette
    // between the state change and the keystroke. The refusal is re-read from
    // `enabled()` inside `registry.run` at the moment of invocation, not taken
    // from the render that drew the row — which is the guarantee that matters,
    // because the row on screen is always at least one event old.
    act(() => useUiStore.setState({ viewMode: '2d' }));
    press(searchInput(), 'Enter');
    expect(useUiStore.getState().activeTool).toBe('wall');
    expect(palette()).toBeNull();
  });

  it('re-draws the row as available on the next render', () => {
    mountLayer();
    act(() => useUiStore.setState({ viewMode: '3d', activeTool: 'select' }));
    pressModK(document);
    typeQuery('draw wall');
    expect(rows()[0]?.getAttribute('aria-disabled')).toBe('true');

    // `aria-disabled` is a picture of `enabled()` taken when the row was drawn,
    // so it refreshes on the next render rather than the instant the store
    // moves. Any keystroke in the palette is such a render, which bounds the
    // staleness to one character; `run()` is authoritative in the meantime.
    act(() => useUiStore.setState({ viewMode: '2d' }));
    typeQuery('draw walls');
    expect(rows()[0]?.textContent).toContain('Draw walls');
    expect(rows()[0]?.getAttribute('aria-disabled')).toBe('false');
  });
});

describe('the cheatsheet', () => {
  it('opens on mod+/ and lists every bound command, grouped', () => {
    mountLayer();
    press(document, '/', { ctrlKey: true });
    const sheet = cheatsheet();
    expect(sheet).not.toBeNull();

    const text = sheet?.textContent ?? '';
    for (const command of registry.boundCommands()) {
      expect(text, `${command.id} is bound but missing from the cheatsheet`).toContain(
        command.title,
      );
    }
    // Including the two keys `components/ShortcutsDialog` structurally cannot
    // show, because they are not in KEY_BINDINGS.
    expect(text).toContain('Ctrl+K');
    expect(text).toContain('Ctrl+/');
    // …and the fixed map's own keys, mirrored in.
    expect(text).toContain('Draw walls');
    expect(text).toContain('?');

    // Grouped: every group with rows renders as a labelled section.
    const sections = [...(sheet?.querySelectorAll('section') ?? [])];
    expect(sections.length).toBeGreaterThan(2);
    expect(sections.map((s) => s.getAttribute('aria-label'))).toContain('Tools');
  });

  it('closes on Escape', () => {
    mountLayer();
    press(document, '/', { ctrlKey: true });
    expect(cheatsheet()).not.toBeNull();
    press(document, 'Escape');
    expect(cheatsheet()).toBeNull();
  });
});

describe('the ? key, which PlanPage still owns', () => {
  it('is left alone by default, so nothing double-fires on the Plan tab', () => {
    mountLayer();
    press(document, '?', { shiftKey: true });
    expect(cheatsheet()).toBeNull();
  });

  it('opens this cheatsheet once handed over with ownHelpKey', () => {
    mountLayer({ ownHelpKey: true });
    press(document, '?', { shiftKey: true });
    expect(cheatsheet()).not.toBeNull();
    // The unshifted form too — some layouts put ? on its own key, which is why
    // lib/keymap.ts registers both.
    press(document, 'Escape');
    press(document, '?');
    expect(cheatsheet()).not.toBeNull();
  });
});

describe('teardown', () => {
  it('stops listening once unmounted', () => {
    mountLayer();
    act(() => root.unmount());
    pressModK(document);
    expect(palette()).toBeNull();
    root = createRoot(container);
  });
});
