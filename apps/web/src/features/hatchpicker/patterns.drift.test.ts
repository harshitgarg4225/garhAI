/**
 * patterns.drift.test.ts — THE gate on the mirror.
 *
 * `patterns.ts` is a second copy of a table whose first copy is Python. This
 * repo has already paid for two hand-kept copies of exactly this table: earth
 * rendered as cross, hatches 31x too dense, the angle applied twice. So the
 * copy is allowed to exist only because this spec reads
 * `services/drawings/render/hatch_patterns.py` on every run and refuses to let
 * the two differ — in keys, in order, in labels, in ACAD names, or in a single
 * float of geometry.
 *
 * The last three cases below are the spec's own negative controls, and they
 * run in CI beside the gate rather than living in a comment: each mutates the
 * REAL Python source in memory (a changed angle, a dropped pattern, an extra
 * one) and asserts the comparison notices. A drift gate whose reader silently
 * returned `[]`, or ignored `lines`, would pass the first test in this file
 * and fail those — which is the whole point of having them.
 */

import { describe, expect, it } from 'vitest';

import {
  HATCH_PATTERNS,
  HATCH_PATTERN_KEYS,
  hatchPattern,
  isHatchPatternKey,
  isSolidPattern,
  type HatchPatternDef,
} from './patterns';
import { parseHatchDefs, readHatchPatternsSource, readPythonHatchDefs } from './pythonDefs';

/** Strip the readonly tuples down to what `toEqual` compares structurally. */
function plain(defs: readonly HatchPatternDef[]): unknown {
  return defs.map((definition) => ({
    key: definition.key,
    acadName: definition.acadName,
    label: definition.label,
    lines: definition.lines.map((line) => ({
      angleDeg: line.angleDeg,
      base: [line.base[0], line.base[1]],
      offset: [line.offset[0], line.offset[1]],
      dashes: [...line.dashes],
    })),
  }));
}

describe('the TS mirror matches HATCH_DEFS in hatch_patterns.py', () => {
  it('carries the same patterns, in the same order', () => {
    expect(readPythonHatchDefs().map((d) => d.key)).toEqual(HATCH_PATTERNS.map((d) => d.key));
  });

  it('deep-equals every definition — names, labels and every float of geometry', () => {
    // Exact equality, not a tolerance: both sides carry the shortest
    // round-trip decimal of the same IEEE-754 double, so any difference at all
    // is a real edit that a human made in one file and not the other.
    expect(plain(readPythonHatchDefs() as unknown as readonly HatchPatternDef[])).toEqual(
      plain(HATCH_PATTERNS),
    );
  });

  it('key union, table and Python agree on the list of keys', () => {
    expect([...HATCH_PATTERN_KEYS]).toEqual(HATCH_PATTERNS.map((d) => d.key));
    expect([...HATCH_PATTERN_KEYS]).toEqual(readPythonHatchDefs().map((d) => d.key));
    // Fifteen patterns exist in the drawing engine; the picker is worth
    // building because an architect could reach none of them. If that count
    // moves, both this file and the picker's copy have to be looked at.
    expect(HATCH_PATTERN_KEYS).toHaveLength(15);
  });

  it('solid is the only fill without line geometry', () => {
    const withoutLines = HATCH_PATTERNS.filter((d) => d.lines.length === 0).map((d) => d.key);
    expect(withoutLines).toEqual(['solid']);
    expect(isSolidPattern('solid')).toBe(true);
    expect(isSolidPattern('brick')).toBe(false);
  });

  it('lookups are total over the key union and closed to anything else', () => {
    for (const key of HATCH_PATTERN_KEYS) expect(hatchPattern(key).key).toBe(key);
    expect(isHatchPatternKey('brick')).toBe(true);
    expect(isHatchPatternKey('ANSI31')).toBe(false);
    expect(isHatchPatternKey(null)).toBe(false);
  });
});

describe('negative controls — the gate can go red', () => {
  it('notices a changed angle in the Python source', () => {
    // ANSI31's 45 deg is the one number the "angle applied twice" defect moved.
    const source = readHatchPatternsSource().replace(
      'PatternLine(45.0, (0.0, 0.0), (-2.2450640303, 2.2450640303), ()),',
      'PatternLine(30.0, (0.0, 0.0), (-2.2450640303, 2.2450640303), ()),',
    );
    const mutated = parseHatchDefs(source);
    expect(mutated.map((d) => d.key)).toEqual(HATCH_PATTERNS.map((d) => d.key));
    expect(plain(mutated as unknown as readonly HatchPatternDef[])).not.toEqual(
      plain(HATCH_PATTERNS),
    );
  });

  it('notices a pattern REMOVED from the Python source', () => {
    const source = readHatchPatternsSource().replace(
      / {4}"grass": HatchDef\([\s\S]*?\n {4}\),\n/,
      '',
    );
    const mutated = parseHatchDefs(source);
    expect(mutated.map((d) => d.key)).not.toContain('grass');
    expect(mutated).toHaveLength(HATCH_PATTERNS.length - 1);
  });

  it('notices a pattern ADDED to the Python source — the drift this exists for', () => {
    // The failure mode named in the task: a sixteenth pattern lands in Python
    // and the UI never offers it. The mirror must go red, not quietly show 15.
    const source = readHatchPatternsSource().replace(
      '\n}\n\n\n#: A zero-length ACAD dash',
      '\n    "rubble": HatchDef(\n' +
        '        key="rubble",\n' +
        '        acad_name="AR-RROOF",\n' +
        '        label="Rubble",\n' +
        '        lines=(PatternLine(30.0, (0.0, 0.0), (-2.0, 2.0), ()),),\n' +
        '    ),\n}\n\n\n#: A zero-length ACAD dash',
    );
    const mutated = parseHatchDefs(source);
    expect(mutated.map((d) => d.key)).toContain('rubble');
    expect(mutated).toHaveLength(HATCH_PATTERNS.length + 1);
  });

  it('refuses a source it cannot read rather than returning nothing', () => {
    expect(() => parseHatchDefs('# no table here\n')).toThrow(/HATCH_DEFS/);
    expect(() =>
      parseHatchDefs('HATCH_DEFS = {\n    "x": HatchDef(key=MISSING_NAME, acad_name="A",'),
    ).toThrow(/MISSING_NAME/);
  });
});
