/**
 * binding.ts — binding strings (`'mod+k'`, `'shift+?'`, `'mod+shift+p'`) parsed
 * once, matched against real `KeyboardEvent`s, and formatted for display.
 *
 * `lib/keymap.ts` already owns the app's fixed key table, and it describes a
 * binding as a `{ key, modifiers: 'none' | 'shift' | 'mod' | 'mod+shift' }`
 * pair. That closed enum is exactly right for a hand-maintained array and
 * exactly wrong for a registry that anything in the app may register into: a
 * feature adding a command should be able to write `'mod+shift+e'` without
 * first adding a member to a union in `lib/`. This module is the open form of
 * the same idea, and it is deliberately compatible — `bindingSpecOf()` turns a
 * `KeyBinding` from `lib/keymap.ts` into a spec string, and
 * `registry.ts` uses `keymap.matchBinding` itself as the oracle for "does the
 * app's fixed table already claim this key".
 *
 * ## Two rules that are not obvious
 *
 * **`mod` is the platform's primary modifier and nothing else.** On macOS that
 * is Command; everywhere else it is Control. A binding that says `mod` must NOT
 * also fire when the *other* one is held — `Ctrl+K` on a Mac belongs to the
 * terminal-style line-editing bindings macOS has had since NeXT, and quietly
 * stealing it is the kind of thing that makes an app feel hostile.
 *
 * **Shift is matched leniently for punctuation keys, and strictly for letters.**
 * `?` is Shift+/ on a US layout and an unshifted key on several others, so
 * `event.shiftKey` carries no information about whether the user "meant" shift:
 * the glyph in `event.key` already is the evidence. `lib/keymap.ts` works
 * around this by registering `?` twice, once shifted and once not. Here the
 * lenient rule is expressed once, in {@link matchesBinding}, and it is safe
 * precisely because a shifted punctuation key produces a DIFFERENT `event.key`
 * (Shift+`/` is `?`, not `/`), so no two punctuation bindings can collide
 * through it. Letters and digits keep the strict rule: `shift+a` requires
 * Shift, because `event.key` is lower-cased before comparison and would
 * otherwise make `a` and `Shift+A` indistinguishable.
 */

/**
 * Named keys we accept in a spec, mapped to the exact `KeyboardEvent.key` value
 * the browser reports. Anything not here and not a single character is a typo,
 * and {@link parseBinding} throws rather than registering a binding that can
 * never fire — a binding that silently never matches is this repository's
 * bug class 1 with a keyboard attached.
 */
const NAMED_KEYS: Readonly<Record<string, string>> = {
  escape: 'Escape',
  esc: 'Escape',
  enter: 'Enter',
  return: 'Enter',
  tab: 'Tab',
  space: ' ',
  backspace: 'Backspace',
  delete: 'Delete',
  del: 'Delete',
  up: 'ArrowUp',
  down: 'ArrowDown',
  left: 'ArrowLeft',
  right: 'ArrowRight',
  arrowup: 'ArrowUp',
  arrowdown: 'ArrowDown',
  arrowleft: 'ArrowLeft',
  arrowright: 'ArrowRight',
  home: 'Home',
  end: 'End',
  pageup: 'PageUp',
  pagedown: 'PageDown',
};

/** How a named key is written back out for a human. `' '` needs a word. */
const KEY_LABELS: Readonly<Record<string, string>> = {
  ' ': 'Space',
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  Escape: 'Esc',
  Backspace: '⌫',
  Delete: 'Del',
};

export interface ParsedBinding {
  /** The spec it was parsed from, verbatim — quoted in every error message. */
  readonly source: string;
  /** Normalised `KeyboardEvent.key`: single characters lower-cased. */
  readonly key: string;
  /** Platform primary modifier: Command on macOS, Control elsewhere. */
  readonly mod: boolean;
  /** Literal Control, requested as `ctrl` rather than `mod`. */
  readonly ctrl: boolean;
  /** Literal Command, requested as `cmd`/`meta` rather than `mod`. */
  readonly meta: boolean;
  readonly shift: boolean;
  readonly alt: boolean;
  /**
   * True when {@link key} is punctuation, in which case Shift is matched
   * leniently. See the header — this is the `?` problem, solved once.
   */
  readonly shiftIsGlyph: boolean;
}

export class BindingSyntaxError extends Error {
  constructor(spec: string, reason: string) {
    super(`Bad key binding ${JSON.stringify(spec)}: ${reason}`);
    this.name = 'BindingSyntaxError';
  }
}

/**
 * Split a spec into `[...modifiers, key]`.
 *
 * `'+'` is both the separator and a perfectly ordinary key, so `'mod++'` splits
 * to `['mod', '', '']` and the empty tail has to be folded back into a literal
 * plus. Without this a zoom binding written the obvious way would parse to an
 * empty key and never fire.
 */
function tokenise(spec: string): string[] {
  const parts = spec.split('+');
  const last = parts.length - 1;
  if (parts.length >= 2 && parts[last] === '' && parts[last - 1] === '') {
    parts.splice(last - 1, 2, '+');
  }
  return parts;
}

/**
 * Parse a binding spec. Throws {@link BindingSyntaxError} on anything it does
 * not understand.
 *
 * Throwing is the design. The alternative — returning null and skipping the
 * binding — produces a command that is registered, listed, and permanently
 * unreachable by keyboard, which is precisely the failure this feature exists
 * to make impossible.
 */
export function parseBinding(spec: string): ParsedBinding {
  const trimmed = spec.trim();
  if (trimmed === '') throw new BindingSyntaxError(spec, 'empty');

  const tokens = tokenise(trimmed);
  const rawKey = tokens[tokens.length - 1] ?? '';
  if (rawKey === '') throw new BindingSyntaxError(spec, 'no key after the modifiers');

  let mod = false;
  let ctrl = false;
  let meta = false;
  let shift = false;
  let alt = false;

  for (const token of tokens.slice(0, -1)) {
    switch (token.trim().toLowerCase()) {
      case 'mod':
        mod = true;
        break;
      case 'ctrl':
      case 'control':
        ctrl = true;
        break;
      case 'cmd':
      case 'meta':
      case 'command':
        meta = true;
        break;
      case 'shift':
        shift = true;
        break;
      case 'alt':
      case 'option':
        alt = true;
        break;
      default:
        throw new BindingSyntaxError(spec, `unknown modifier ${JSON.stringify(token)}`);
    }
  }

  // `mod` already means "Command here, Control there". Combining it with either
  // literal is a spec that means two different things on two platforms, and the
  // author almost certainly meant one of them.
  if (mod && (ctrl || meta)) {
    throw new BindingSyntaxError(spec, 'mod cannot be combined with ctrl or cmd');
  }

  const key = normaliseKey(spec, rawKey);
  return {
    source: trimmed,
    key,
    mod,
    ctrl,
    meta,
    shift,
    alt,
    shiftIsGlyph: key.length === 1 && !/[a-z0-9]/.test(key),
  };
}

function normaliseKey(spec: string, rawKey: string): string {
  if (rawKey.length === 1) return rawKey.toLowerCase();
  const named = NAMED_KEYS[rawKey.toLowerCase()];
  if (named !== undefined) return named;
  // Function keys are regular enough to accept without a table entry each.
  const fn = /^f([1-9]|1[0-2])$/i.exec(rawKey);
  if (fn) return `F${fn[1] ?? ''}`;
  throw new BindingSyntaxError(
    spec,
    `unknown key ${JSON.stringify(rawKey)} — use a single character, a function key, ` +
      `or one of: ${Object.keys(NAMED_KEYS).join(', ')}`,
  );
}

/** The shape of a keyboard event this module needs. Keeps tests DOM-free. */
export type KeyEventLike = Pick<
  KeyboardEvent,
  'key' | 'metaKey' | 'ctrlKey' | 'shiftKey' | 'altKey'
>;

export interface MatchContext {
  /** Override platform detection. Tests pass it; the app never does. */
  readonly mac?: boolean;
}

/** Same detection `lib/keymap.ts` uses, so the two layers agree on `mod`. */
export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);
}

/** Normalise an event's key the way {@link parseBinding} normalises a spec. */
export function normaliseEventKey(key: string): string {
  return key.length === 1 ? key.toLowerCase() : key;
}

/** Does this event mean this binding? Pure — no DOM, no platform sniffing. */
export function matchesBinding(
  event: KeyEventLike,
  binding: ParsedBinding,
  context: MatchContext = {},
): boolean {
  if (normaliseEventKey(event.key) !== binding.key) return false;

  // Alt is reserved: on many layouts it composes characters, and treating it as
  // "don't care" would make every unmodified binding fire mid-composition.
  // `lib/keymap.ts` takes the same line.
  if (event.altKey !== binding.alt) return false;

  const mac = context.mac ?? isMacPlatform();
  if (binding.mod) {
    const primary = mac ? event.metaKey : event.ctrlKey;
    const secondary = mac ? event.ctrlKey : event.metaKey;
    if (!primary || secondary) return false;
  } else {
    if (event.ctrlKey !== binding.ctrl) return false;
    if (event.metaKey !== binding.meta) return false;
  }

  if (!binding.shiftIsGlyph && event.shiftKey !== binding.shift) return false;
  return true;
}

/**
 * Would these two bindings both fire on the same keystroke?
 *
 * Used by the registry to refuse a second command on a key that is already
 * taken. Conflicts are checked on BOTH platforms, because a binding that is
 * unambiguous on Linux and ambiguous on macOS is still a bug — it just is not
 * yours yet.
 */
export function bindingsCollide(a: ParsedBinding, b: ParsedBinding): boolean {
  if (a.key !== b.key) return false;
  return [true, false].some((mac) => {
    const event = synthesiseEvent(a, mac);
    return matchesBinding(event, b, { mac });
  });
}

/**
 * The keystroke a binding describes, as an event-shaped object.
 *
 * This is what lets the registry ask `lib/keymap.ts`'s own matcher whether the
 * app's fixed table already claims a key, instead of maintaining a second
 * opinion about what that table contains.
 */
export function synthesiseEvent(
  binding: ParsedBinding,
  mac: boolean,
): KeyEventLike & { readonly repeat: boolean } {
  return {
    key: binding.key,
    metaKey: binding.meta || (binding.mod && mac),
    ctrlKey: binding.ctrl || (binding.mod && !mac),
    shiftKey: binding.shift,
    altKey: binding.alt,
    repeat: false,
  };
}

/** `⌘K` on macOS, `Ctrl+K` elsewhere. Apple's modifier order is ⌃⌥⇧⌘. */
export function formatBinding(binding: ParsedBinding, mac = isMacPlatform()): string {
  const label =
    KEY_LABELS[binding.key] ?? (binding.key.length === 1 ? binding.key.toUpperCase() : binding.key);

  // A punctuation binding prints as its glyph alone. "⇧?" is noise: the ? IS
  // the shifted key, and showing both invites someone to press three keys.
  const wantsShift = binding.shift && !binding.shiftIsGlyph;

  if (mac) {
    const prefix =
      (binding.ctrl ? '⌃' : '') +
      (binding.alt ? '⌥' : '') +
      (wantsShift ? '⇧' : '') +
      (binding.meta || binding.mod ? '⌘' : '');
    return `${prefix}${label}`;
  }

  const parts: string[] = [];
  if (binding.ctrl || binding.mod) parts.push('Ctrl');
  if (binding.meta) parts.push('Cmd');
  if (binding.alt) parts.push('Alt');
  if (wantsShift) parts.push('Shift');
  parts.push(label);
  return parts.join('+');
}
