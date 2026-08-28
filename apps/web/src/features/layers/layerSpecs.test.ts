/**
 * THE DRIFT GATE.
 *
 * `services/drawings/layers.py` is the source of the nine §7 layer names and
 * their CAD properties; `layerSpecs.ts` is a hand-written mirror of it, because
 * the browser cannot import Python. Two tables of the same facts drift — that
 * is not a risk, it is a certainty, and the drift is invisible: the web panel
 * would keep saying "A-WIND, cyan" long after the DXF started writing something
 * else, and nothing would be red.
 *
 * So this test parses the Python file itself. Not a JSON export of it, not a
 * fixture snapshot taken once — the actual `LAYERS` tuple, at test time. Change
 * a lineweight in Python and this goes red until the mirror follows.
 *
 * THREE THINGS THIS FILE DOES SO IT CANNOT BECOME A TEST THAT PASSES VACUOUSLY
 * (bug class 3 — the PII test that seeded a field the summariser never read):
 *
 *  1. A missing `layers.py` FAILS. It does not skip. A drawings service that
 *     has moved or been deleted is itself the drift this test exists to catch,
 *     and a green skip would hide it.
 *  2. The parser's own yield is asserted before it is compared — at least nine
 *     `LayerSpec(...)` calls, at least eight `A_*` name constants, at least
 *     eight `_ACI_*` values. A regex that silently matches nothing would
 *     otherwise make every comparison below trivially true.
 *  3. Both directions are checked. Same length, same order, same values — so a
 *     layer added on either side is a failure, not a quiet omission.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  CANVAS_DRAWING_LAYERS,
  DRAWING_LAYER_NAMES,
  DRAWING_LAYER_SPECS,
  aciSwatchHex,
} from './layerSpecs';

/**
 * `apps/web/src/features/layers/` → the repo root is five levels up. Anchored
 * on this file rather than on `process.cwd()` because vitest's cwd is
 * `apps/web` and a plain relative path would break the moment someone ran the
 * suite from the repo root.
 *
 * `path.resolve` and NOT `new URL('…', import.meta.url)`: Vite rewrites that
 * exact expression at transform time into an asset URL served by the dev
 * server (`http://localhost:3000/@fs/…`), and `fileURLToPath` then rejects it
 * for not being a file: URL. Going through `dirname(fileURLToPath(...))` keeps
 * the resolution in Node's hands.
 */
const LAYERS_PY = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../../services/drawings/layers.py',
);

interface PythonLayer {
  readonly name: string;
  readonly aci: number;
  readonly linetype: string;
  readonly lineweight: number;
  readonly description: string;
  readonly plottable: boolean;
}

interface ParsedPython {
  readonly layers: readonly PythonLayer[];
  readonly nameConstants: number;
  readonly aciConstants: number;
}

function readLayersPy(): string {
  try {
    return readFileSync(LAYERS_PY, 'utf8');
  } catch (cause) {
    throw new Error(
      `Could not read the authoritative layer table at ${LAYERS_PY}. ` +
        'It is the source `layerSpecs.ts` mirrors; if it moved, this mirror is ' +
        'unverifiable and the move is the thing to fix.',
      { cause },
    );
  }
}

/**
 * Parse `layers.py`'s constants and its `LAYERS` tuple.
 *
 * Deliberately strict: it resolves the `A_*` and `_ACI_*` identifiers rather
 * than accepting whatever is written in the tuple, so renaming a constant
 * without renaming its use is caught too. Anything the shapes below do not
 * match is dropped, which is why the caller asserts on the counts.
 */
function parseLayersPy(source: string): ParsedPython {
  const names = new Map<string, string>();
  for (const match of source.matchAll(/^(A_[A-Z0-9_]+)\s*=\s*"([^"]+)"$/gm)) {
    names.set(match[1] as string, match[2] as string);
  }

  const aci = new Map<string, number>();
  for (const match of source.matchAll(/^(_ACI_[A-Z0-9_]+)\s*=\s*(\d+)$/gm)) {
    aci.set(match[1] as string, Number.parseInt(match[2] as string, 10));
  }

  // Only the LAYERS tuple, so a LayerSpec(...) written in a docstring or a
  // future secondary table cannot leak into the comparison.
  const tuple = /LAYERS:\s*tuple\[LayerSpec,\s*\.\.\.\]\s*=\s*\(([\s\S]*?)\n\)/.exec(source);
  if (tuple === null) {
    throw new Error(
      'Could not find the `LAYERS: tuple[LayerSpec, ...] = (...)` block in layers.py. ' +
        'The table was restructured; this parser must be updated to match before the ' +
        'mirror can be trusted again.',
    );
  }

  const layers: PythonLayer[] = [];
  const call =
    /LayerSpec\(\s*([A-Z_][A-Z0-9_]*),\s*([A-Z_][A-Z0-9_]*),\s*"([^"]*)",\s*(-?\d+),\s*"([^"]*)"\s*(?:,\s*(True|False))?\s*,?\s*\)/g;
  for (const match of (tuple[1] as string).matchAll(call)) {
    const nameConst = match[1] as string;
    const aciConst = match[2] as string;
    const resolvedName = names.get(nameConst);
    const resolvedAci = aci.get(aciConst);
    if (resolvedName === undefined || resolvedAci === undefined) {
      throw new Error(
        `LAYERS references ${resolvedName === undefined ? nameConst : aciConst}, which is not ` +
          'defined as a module constant in layers.py. The parser resolves identifiers on ' +
          'purpose — a renamed constant must not slip through as an opaque string.',
      );
    }
    layers.push({
      name: resolvedName,
      aci: resolvedAci,
      linetype: match[3] as string,
      lineweight: Number.parseInt(match[4] as string, 10),
      description: match[5] as string,
      plottable: match[6] !== 'False',
    });
  }

  return { layers, nameConstants: names.size, aciConstants: aci.size };
}

const parsed = parseLayersPy(readLayersPy());

describe('the parser itself (so nothing below can pass vacuously)', () => {
  it('resolved the module constants', () => {
    // Nine layers, but A-WALL and A-TITL share ACI 7, so eight distinct colour
    // constants is the floor. The names are nine distinct `A_*` identifiers.
    expect(parsed.nameConstants).toBeGreaterThanOrEqual(9);
    expect(parsed.aciConstants).toBeGreaterThanOrEqual(8);
  });

  it('found the nine LayerSpec entries', () => {
    expect(parsed.layers.length).toBeGreaterThanOrEqual(9);
  });

  it('read real values, not empty strings', () => {
    for (const layer of parsed.layers) {
      expect(layer.name).toMatch(/^A-[A-Z-]+$/);
      expect(layer.linetype.length).toBeGreaterThan(0);
      expect(layer.description.length).toBeGreaterThan(0);
      expect(Number.isInteger(layer.aci)).toBe(true);
    }
  });
});

describe('TS mirror agrees with services/drawings/layers.py', () => {
  it('has the same number of layers', () => {
    expect(DRAWING_LAYER_SPECS.length).toBe(parsed.layers.length);
  });

  it('has the same names in the same order', () => {
    // Order is not cosmetic: it is the DXF layer-creation order and the order
    // the golden sheet files were generated in.
    expect(DRAWING_LAYER_SPECS.map((s) => s.name)).toEqual(parsed.layers.map((l) => l.name));
    expect([...DRAWING_LAYER_NAMES]).toEqual(parsed.layers.map((l) => l.name));
  });

  it('agrees on colour, linetype, lineweight, description and plottability', () => {
    for (const [index, python] of parsed.layers.entries()) {
      const ts = DRAWING_LAYER_SPECS[index];
      expect(ts, `no TS spec at index ${index} for ${python.name}`).toBeDefined();
      if (ts === undefined) continue;
      expect({ layer: python.name, ...pick(ts) }).toEqual({
        layer: python.name,
        aci: python.aci,
        linetype: python.linetype,
        lineweightHundredthsMm: python.lineweight,
        description: python.description,
        plottable: python.plottable,
      });
    }
  });
});

function pick(spec: (typeof DRAWING_LAYER_SPECS)[number]): {
  aci: number;
  linetype: string;
  lineweightHundredthsMm: number;
  description: string;
  plottable: boolean;
} {
  return {
    aci: spec.aci,
    linetype: spec.linetype,
    lineweightHundredthsMm: spec.lineweightHundredthsMm,
    description: spec.description,
    plottable: spec.plottable,
  };
}

describe('the canvas subset', () => {
  it('is exactly the layers marked onCanvas, in the same order', () => {
    // `mapping.test.ts` holds each of these to "hiding it changes the drawing";
    // this pins the list they are drawn from so the two cannot diverge.
    expect([...CANVAS_DRAWING_LAYERS]).toEqual(
      DRAWING_LAYER_SPECS.filter((s) => s.onCanvas).map((s) => s.name),
    );
    expect(CANVAS_DRAWING_LAYERS).not.toContain('A-TITL');
    expect(CANVAS_DRAWING_LAYERS.length).toBe(DRAWING_LAYER_SPECS.length - 1);
  });
});

describe('swatch colours', () => {
  it('gives every ACI in the table a colour, except 7', () => {
    for (const spec of DRAWING_LAYER_SPECS) {
      const hex = aciSwatchHex(spec.aci);
      if (spec.aci === 7) expect(hex).toBeNull();
      else expect(hex).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('returns null for an index outside the table rather than guessing', () => {
    expect(aciSwatchHex(251)).toBeNull();
  });
});
