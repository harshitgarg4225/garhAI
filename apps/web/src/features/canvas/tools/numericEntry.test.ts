/**
 * Spec for the §12 requirement that gets skipped most often: **typing a number
 * overrides the mouse**.
 *
 * The interesting part is not the parse — `units.ts` is golden-tested against
 * the Python mirror already — it is the KEY OWNERSHIP rule. `3` is the
 * second-floor shortcut, `m` is the measure tool, and `3.8m` is a length made
 * entirely of those keys. `wantsKey` is where that collision is resolved, so it
 * is where most of these assertions live.
 */

import { describe, expect, it } from 'vitest';

import {
  activeField,
  clearEntry,
  createEntry,
  entryError,
  entryValueFor,
  entryView,
  feedKey,
  formatEcho,
  isEntryActive,
  isEntryApplicable,
  parseEntry,
  resetBuffer,
  wantsKey,
  type NumericEntryState,
  type NumericField,
} from './numericEntry';
import { key } from './toolTestKit';

const LENGTH: NumericField = { id: 'length', label: 'Length', unit: 'mm', minMm: 1 };
const WIDTH: NumericField = { id: 'width', label: 'Width', unit: 'mm', minMm: 300, maxMm: 6000 };
const ROTATION: NumericField = { id: 'rotation', label: 'Rotation', unit: 'deg' };

/** A state with `text` already typed into the first field. */
function typed(fields: readonly NumericField[], text: string): NumericEntryState {
  let state = createEntry(fields);
  for (const ch of text) state = feedKey(state, key(ch)).state;
  return state;
}

describe('key ownership — the §12 collision', () => {
  const empty = createEntry([LENGTH]);

  it('claims digits and length punctuation as soon as there is a field', () => {
    for (const k of ['0', '5', '9', '.', "'", '"', '/', '-']) {
      expect(wantsKey(empty, key(k))).toBe(true);
    }
  });

  it('does NOT claim a unit letter on an empty buffer — `m` is still the measure tool', () => {
    expect(wantsKey(empty, key('m'))).toBe(false);
    expect(wantsKey(empty, key('f'))).toBe(false);
  });

  it('DOES claim a unit letter once a number is being typed — `3.8m` is metres', () => {
    const state = typed([LENGTH], '3.8');
    expect(wantsKey(state, key('m'))).toBe(true);
  });

  it('never claims a modified key, so ⌘Z stays undo while drawing', () => {
    expect(wantsKey(empty, key('3', { metaKey: true }))).toBe(false);
    expect(wantsKey(empty, key('3', { ctrlKey: true }))).toBe(false);
    expect(wantsKey(empty, key('3', { altKey: true }))).toBe(false);
  });

  it('claims Backspace only while there is something to erase', () => {
    expect(wantsKey(empty, key('Backspace'))).toBe(false);
    expect(wantsKey(typed([LENGTH], '36'), key('Backspace'))).toBe(true);
  });

  it('claims Tab only when there is more than one field to cycle', () => {
    expect(wantsKey(empty, key('Tab'))).toBe(false);
    expect(wantsKey(createEntry([LENGTH, WIDTH]), key('Tab'))).toBe(true);
  });

  it('claims nothing at all when the tool declared no fields', () => {
    const none = createEntry([]);
    expect(wantsKey(none, key('3'))).toBe(false);
    expect(activeField(none)).toBeNull();
  });

  it('leaves every other letter alone', () => {
    for (const k of ['v', 'w', 'b', 'g', 'x', 'z']) {
      expect(wantsKey(empty, key(k))).toBe(false);
      expect(wantsKey(typed([LENGTH], '3'), key(k))).toBe(false);
    }
  });
});

describe('transitions', () => {
  it('accumulates typed characters', () => {
    const state = typed([LENGTH], '3600');
    expect(state.buffer).toBe('3600');
    expect(isEntryActive(state)).toBe(true);
  });

  it('reports `ignored` for a key it does not own, and does not change state', () => {
    const before = createEntry([LENGTH]);
    const step = feedKey(before, key('m'));
    expect(step.action).toBe('ignored');
    expect(step.state).toBe(before);
  });

  it('Backspace erases, and reports `cleared` on the last character', () => {
    const state = typed([LENGTH], '36');
    const once = feedKey(state, key('Backspace'));
    expect(once.action).toBe('typed');
    expect(once.state.buffer).toBe('3');
    const twice = feedKey(once.state, key('Backspace'));
    expect(twice.action).toBe('cleared');
    expect(twice.state.buffer).toBe('');
  });

  it('Tab moves to the next field and drops the buffer it belonged to', () => {
    const state = typed([LENGTH, WIDTH], '900');
    const step = feedKey(state, key('Tab'));
    expect(step.action).toBe('field');
    expect(activeField(step.state)?.id).toBe('width');
    expect(step.state.buffer).toBe('');
  });

  it('Shift-Tab walks backwards and wraps', () => {
    const state = createEntry([LENGTH, WIDTH]);
    const back = feedKey(state, key('Tab', { shiftKey: true }));
    expect(activeField(back.state)?.id).toBe('width');
  });

  it('clearEntry returns null when there is nothing to clear — the escape ladder', () => {
    // BaseTool relies on this exact signal: null means "Esc was not consumed by
    // the buffer, so it belongs to the drawing".
    expect(clearEntry(createEntry([LENGTH]))).toBeNull();
    expect(clearEntry(typed([LENGTH], '36'))?.buffer).toBe('');
  });

  it('resetBuffer is identity when the buffer is already empty', () => {
    const state = createEntry([LENGTH]);
    expect(resetBuffer(state)).toBe(state);
  });
});

describe('parsing — a bare number is millimetres, per §12', () => {
  it('reads 3600 as 3600 mm even in a ft-in project', () => {
    expect(parseEntry(typed([LENGTH], '3600'))?.value).toBe(3600);
  });

  it("reads 12' as twelve feet", () => {
    expect(parseEntry(typed([LENGTH], "12'"))?.value).toBe(3658);
  });

  it('reads 12\'6" as twelve foot six', () => {
    expect(parseEntry(typed([LENGTH], '12\'6"'))?.value).toBe(3810);
  });

  it('reads 3.8m as metres', () => {
    expect(parseEntry(typed([LENGTH], '3.8m'))?.value).toBe(3800);
  });

  it('returns null while the text is still incomplete', () => {
    expect(parseEntry(typed([LENGTH], '3.'))).toBeNull();
    expect(parseEntry(createEntry([LENGTH]))).toBeNull();
  });

  it('parses a degree field as a plain number, never as feet', () => {
    const state = typed([ROTATION], '90');
    expect(parseEntry(state)?.value).toBe(90);
    // `12'` is a length, not an angle: the degree path refuses it outright.
    expect(parseEntry(typed([ROTATION], "12'"))).toBeNull();
  });

  it('accepts a negative angle', () => {
    expect(parseEntry(typed([ROTATION], '-90'))?.value).toBe(-90);
  });
});

describe('bounds and errors', () => {
  it('says nothing while a trailing separator means "still typing"', () => {
    // Neither of these parses yet, and neither is a mistake — nagging someone
    // mid-keystroke is how a numeric field stops being used.
    expect(entryError(typed([LENGTH], '3.'))).toBeNull();
    expect(entryError(typed([LENGTH], '12-'))).toBeNull();
  });

  it('explains an unparseable buffer in the units the parser accepts', () => {
    const state: NumericEntryState = { fields: [LENGTH], index: 0, buffer: '3q' };
    expect(entryError(state)).toContain('3600');
  });

  it('refuses a value under the field minimum, and reports it as inapplicable', () => {
    const state = typed([WIDTH], '100');
    expect(entryError(state)).toContain('at least');
    expect(isEntryApplicable(state)).toBe(false);
    expect(entryValueFor(state, 'width')).toBeNull();
  });

  it('refuses a value over the field maximum', () => {
    const state = typed([WIDTH], '9000');
    expect(entryError(state)).toContain('at most');
    expect(isEntryApplicable(state)).toBe(false);
  });

  it('accepts a value inside the bounds', () => {
    const state = typed([WIDTH], '1200');
    expect(entryError(state)).toBeNull();
    expect(entryValueFor(state, 'width')).toBe(1200);
  });

  it('answers null for a field that is not the active one', () => {
    // A tool asking "does the typed value apply to MY field?" must get a clean
    // no, never a number meant for a different field.
    const state = typed([LENGTH, WIDTH], '3600');
    expect(entryValueFor(state, 'length')).toBe(3600);
    expect(entryValueFor(state, 'width')).toBeNull();
  });
});

describe('the inline view', () => {
  it('is null until something has been typed', () => {
    expect(entryView(createEntry([LENGTH]), 'ft-in')).toBeNull();
  });

  it('echoes both forms so the parse is never a guess', () => {
    const view = entryView(typed([LENGTH], '3600'), 'ft-in');
    expect(view?.buffer).toBe('3600');
    expect(view?.value).toBe(3600);
    expect(view?.echo).toContain('3,600 mm');
    expect(view?.error).toBeNull();
  });

  it('lists the fields Tab cycles to', () => {
    const view = entryView(typed([LENGTH, WIDTH], '9'), 'ft-in');
    expect(view?.fields.map((f) => f.id)).toEqual(['length', 'width']);
  });

  it('shows an empty echo while the buffer does not parse', () => {
    const view = entryView({ fields: [LENGTH], index: 0, buffer: '3q' }, 'ft-in');
    expect(view?.echo).toBe('');
    expect(view?.value).toBeNull();
  });

  it('formats degrees and counts without pretending they are lengths', () => {
    expect(formatEcho(90, 'deg', 'ft-in')).toBe('90°');
    expect(formatEcho(18, 'count', 'ft-in')).toBe('18');
  });
});
