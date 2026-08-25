/**
 * Keyboard ownership (Phase 5).
 *
 * Two keyboard maps listen on `document`: the app-wide one built here and the
 * canvas tool controller's. `stopPropagation()` does not stop a second
 * listener on the SAME node, so any command both maps acted on would run
 * twice — two undos per ⌘Z, a Tab that toggles 2D→3D→2D and reads as dead.
 * The contract under test: while `ui.toolKeysActive` is up, every overlapping
 * handler in `defaultCommandHandlers()` DECLINES (returns false, so the
 * keystroke is neither acted on nor swallowed), and the moment the flag drops
 * (3D mode, other tabs, editor unmounted) the same handlers act again.
 *
 * These specs call the handlers directly — `matchBinding` and the listener
 * plumbing have their own specs in `keymap.test.ts`; what is new here is only
 * the ownership rule and the 3D scoping of the tool keys.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { KEY_BINDINGS, type CommandId, type KeyBinding } from './keymap';
import { defaultCommandHandlers } from './shortcuts';
import { useUiStore } from '../stores/ui';

function bindingFor(command: CommandId): KeyBinding {
  const binding = KEY_BINDINGS.find((b) => b.command === command);
  if (!binding) throw new Error(`no binding for ${command}`);
  return binding;
}

function fire(command: CommandId): void | boolean {
  const handlers = defaultCommandHandlers();
  const handler = handlers[command];
  if (!handler) throw new Error(`no handler for ${command}`);
  return handler(new KeyboardEvent('keydown'), bindingFor(command));
}

beforeEach(() => {
  useUiStore.setState({
    activeTool: 'select',
    viewMode: '2d',
    toolKeysActive: false,
    snapMode: 'module',
    keyboardEnabled: true,
    modal: null,
    toasts: [],
  });
});

describe('while the tool controller owns the keys (2D editor open)', () => {
  beforeEach(() => {
    useUiStore.getState().setToolKeysActive(true);
  });

  it('every overlapping command declines — one keystroke, one owner', () => {
    const overlapping: CommandId[] = [
      'tool.select',
      'tool.wall',
      'tool.door',
      'tool.window',
      'tool.stair',
      'tool.balcony',
      'tool.measure',
      'tool.furniture',
      'edit.undo',
      'edit.redo',
      'storey.1',
      'storey.2',
      'storey.3',
      'view.toggle',
      'snap.toggle',
      'tool.cancel',
    ];
    for (const command of overlapping) {
      expect(fire(command), `${command} must defer to the tool controller`).toBe(false);
    }
  });

  it('deferring really is inert: Tab does not toggle, W does not arm', () => {
    fire('view.toggle');
    expect(useUiStore.getState().viewMode).toBe('2d');
    fire('tool.wall');
    expect(useUiStore.getState().activeTool).toBe('select');
    fire('snap.toggle');
    expect(useUiStore.getState().snapMode).toBe('module');
  });
});

describe('when the app map is the owner (3D mode, other tabs, no editor)', () => {
  it('Tab toggles the view again — this is how 3D gets back to 2D', () => {
    useUiStore.setState({ viewMode: '3d', toolKeysActive: false });
    fire('view.toggle');
    expect(useUiStore.getState().viewMode).toBe('2d');
  });

  it('tool keys arm in 2D…', () => {
    fire('tool.wall');
    expect(useUiStore.getState().activeTool).toBe('wall');
  });

  it('…but decline in 3D, where W/A/S/D belongs to walking', () => {
    useUiStore.setState({ viewMode: '3d' });
    expect(fire('tool.wall')).toBe(false);
    expect(useUiStore.getState().activeTool).toBe('select');
    expect(fire('tool.stair')).toBe(false);
    expect(fire('tool.door')).toBe(false);
  });

  it('Esc still closes a modal wherever you are', () => {
    useUiStore.setState({ viewMode: '3d' });
    useUiStore.getState().openModal('share');
    expect(fire('tool.cancel')).toBe(true);
    expect(useUiStore.getState().modal).toBeNull();
  });
});
