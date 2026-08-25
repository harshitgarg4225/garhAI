/**
 * Spec for the surface-group → mesh mapping and the material resolver.
 *
 * Three contracts pinned:
 *  1. `surfaceGroupOf` — every element kind the 3D scene can build maps to
 *     exactly one of the model's SURFACE_GROUPS (walked arm by arm);
 *  2. the panel's five picks filter the REAL catalogue fixture
 *     (`fixtures/catalog/materials.json`) into sensible, non-empty shelves —
 *     read from disk so catalogue drift fails here, not in a demo;
 *  3. `resolveAssignment` specificity: element > storey > building, and
 *     within a tier the later document row (later op) wins.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { SURFACE_GROUPS, type MaterialAssignment } from '@garh/model';

import { materialItemSchema, type MaterialItem } from '../../../lib/schemas';
import { fallbackColorFromId, resolveAssignment, resolvedColorHex, swatchHex } from './resolve';
import {
  materialMatchesPick,
  materialsForPick,
  SURFACE_PICKS,
  surfaceGroupOf,
  surfacePickFor,
  type SurfaceElement,
} from './surfaceGroups';

// ---------------------------------------------------------------------------
// 1. Element → group, the whole table
// ---------------------------------------------------------------------------

describe('surfaceGroupOf', () => {
  const CASES: readonly [SurfaceElement, string][] = [
    [{ kind: 'wall', wallKind: 'external' }, 'external_wall'],
    [{ kind: 'wall', wallKind: 'internal' }, 'internal_wall'],
    [{ kind: 'wall', wallKind: 'parapet' }, 'parapet'],
    [{ kind: 'slab', slabKind: 'floor' }, 'floor'],
    [{ kind: 'slab', slabKind: 'terrace' }, 'roof'],
    [{ kind: 'slab', slabKind: 'plinth' }, 'plinth'],
    [{ kind: 'slab', slabKind: 'mumty' }, 'external_wall'],
    [{ kind: 'stair' }, 'staircase'],
    [{ kind: 'balconySlab' }, 'floor'],
    [{ kind: 'balconyRailing' }, 'railing'],
    [{ kind: 'opening', openingKind: 'door' }, 'door'],
    [{ kind: 'opening', openingKind: 'window' }, 'window'],
    [{ kind: 'opening', openingKind: 'ventilator' }, 'window'],
    [{ kind: 'column' }, 'internal_wall'],
    [{ kind: 'facade', componentKind: 'window_trim' }, 'cladding'],
    [{ kind: 'facade', componentKind: 'chajja' }, 'external_wall'],
    [{ kind: 'facade', componentKind: 'parapet_profile' }, 'parapet'],
    [{ kind: 'facade', componentKind: 'cladding_zone' }, 'cladding'],
    [{ kind: 'facade', componentKind: 'porch' }, 'external_wall'],
    [{ kind: 'facade', componentKind: 'railing' }, 'railing'],
    [{ kind: 'facade', componentKind: 'band' }, 'cladding'],
    [{ kind: 'facade', componentKind: 'louver' }, 'cladding'],
    [{ kind: 'facade', componentKind: 'entry_feature' }, 'cladding'],
  ];

  it.each(CASES)('%j → %s', (element, group) => {
    expect(surfaceGroupOf(element)).toBe(group);
  });

  it('only ever returns members of the model enum', () => {
    for (const [element] of CASES) {
      expect(SURFACE_GROUPS).toContain(surfaceGroupOf(element));
    }
  });
});

// ---------------------------------------------------------------------------
// 2. The five picks × the real fixture catalogue
// ---------------------------------------------------------------------------

function loadFixtureCatalogue(): MaterialItem[] {
  const path = fileURLToPath(
    new URL('../../../../../../fixtures/catalog/materials.json', import.meta.url),
  );
  const raw = JSON.parse(readFileSync(path, 'utf8')) as unknown[];
  return raw.map((item) => materialItemSchema.parse(item));
}

describe('SURFACE_PICKS × fixtures/catalog/materials.json', () => {
  const catalogue = loadFixtureCatalogue();
  const byId = new Map(catalogue.map((m) => [m.id, m]));
  const pick = (group: string) => {
    const p = SURFACE_PICKS.find((x) => x.group === group);
    if (p === undefined) throw new Error(`no pick for ${group}`);
    return p;
  };

  it('offers exactly the five task-contract groups', () => {
    expect(SURFACE_PICKS.map((p) => p.group)).toEqual([
      'external_wall',
      'internal_wall',
      'floor',
      'railing',
      'cladding',
    ]);
  });

  it('every pick has materials to offer from the seeded catalogue', () => {
    for (const p of SURFACE_PICKS) {
      expect(materialsForPick(catalogue, p).length).toBeGreaterThan(0);
    }
  });

  it('sorts the known materials onto the right shelves', () => {
    const idsFor = (group: string) => materialsForPick(catalogue, pick(group)).map((m) => m.id);

    const external = idsFor('external_wall');
    expect(external).toContain('exterior-texture');
    expect(external).toContain('exposed-brick');
    expect(external).not.toContain('vitrified-tile-600');
    expect(external).not.toContain('interior-emulsion');

    const internal = idsFor('internal_wall');
    expect(internal).toContain('interior-emulsion');
    expect(internal).toContain('ceramic-wall-tile');
    expect(internal).not.toContain('exterior-texture');

    const floors = idsFor('floor');
    expect(floors).toContain('vitrified-tile-600');
    expect(floors).toContain('granite-flooring');
    expect(floors).toContain('kota-stone');
    expect(floors).not.toContain('ms-railing');

    const railings = idsFor('railing');
    expect(railings).toEqual(
      expect.arrayContaining(['ms-railing', 'ss-railing', 'glass-railing']),
    );
    expect(railings).not.toContain('granite-flooring');

    const trim = idsFor('cladding');
    expect(trim).toContain('acp-panel');
    expect(trim).toContain('wpc-cladding');
    expect(trim).toContain('wooden-louvers');
    expect(trim).not.toContain('interior-emulsion');
  });

  it('an item declaring no application areas is shown, not hidden', () => {
    const bare: MaterialItem = materialItemSchema.parse({ id: 'x', name: 'X' });
    for (const p of SURFACE_PICKS) expect(materialMatchesPick(bare, p)).toBe(true);
  });

  it('surfacePickFor answers for panel groups and declines for the rest', () => {
    expect(surfacePickFor('floor')?.label).toBe('Floors');
    expect(surfacePickFor('door')).toBeNull();
  });

  it('fixture ids referenced by the facade kits stay resolvable', () => {
    // The seeded kits name ms-railing and wpc-cladding (facade-kits.json);
    // the resolver colours them through this same index.
    expect(byId.has('ms-railing')).toBe(true);
    expect(byId.has('wpc-cladding')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. Resolution specificity + procedural swatches
// ---------------------------------------------------------------------------

function assignment(
  id: string,
  group: MaterialAssignment['target']['group'],
  materialId: string,
  storeyId: string | null = null,
  elementId: string | null = null,
): MaterialAssignment {
  return { id, target: { group, storeyId, elementId }, materialId };
}

describe('resolveAssignment', () => {
  const rows: MaterialAssignment[] = [
    assignment('material_A', 'external_wall', 'exterior-texture'),
    assignment('material_B', 'external_wall', 'exposed-brick', 'storey_1'),
    assignment('material_C', 'external_wall', 'stone-cladding', null, 'wall_9'),
    assignment('material_D', 'floor', 'kota-stone'),
  ];

  it('element beats storey beats building', () => {
    expect(
      resolveAssignment(rows, 'external_wall', { storeyId: 'storey_1', elementId: 'wall_9' })?.id,
    ).toBe('material_C');
    expect(
      resolveAssignment(rows, 'external_wall', { storeyId: 'storey_1', elementId: 'wall_2' })?.id,
    ).toBe('material_B');
    expect(
      resolveAssignment(rows, 'external_wall', { storeyId: 'storey_2', elementId: null })?.id,
    ).toBe('material_A');
  });

  it('groups never bleed into each other', () => {
    expect(resolveAssignment(rows, 'floor', { storeyId: 'storey_1', elementId: null })?.id).toBe(
      'material_D',
    );
    expect(resolveAssignment(rows, 'railing', { storeyId: null, elementId: null })).toBeNull();
  });

  it('within a tier, the later document row (later op) wins', () => {
    const twice = [
      assignment('material_A', 'floor', 'kota-stone'),
      assignment('material_E', 'floor', 'granite-flooring'),
    ];
    expect(resolveAssignment(twice, 'floor', { storeyId: null, elementId: null })?.materialId).toBe(
      'granite-flooring',
    );
  });
});

describe('procedural swatches (no texture binaries — inherited fact 4)', () => {
  it('uses the declared colour, normalised to #RRGGBB', () => {
    expect(swatchHex({ id: 'x', colorHex: '#8b6a45' })).toBe('#8B6A45');
    expect(swatchHex({ id: 'x', colorHex: '#abc' })).toBe('#AABBCC');
  });

  it('derives a stable colour when none is declared, per id', () => {
    const a = swatchHex({ id: 'mystery-material', colorHex: null });
    expect(a).toMatch(/^#[0-9A-F]{6}$/);
    expect(swatchHex({ id: 'mystery-material', colorHex: null })).toBe(a);
    expect(swatchHex({ id: 'other-material', colorHex: null })).not.toBe(a);
    expect(fallbackColorFromId('mystery-material')).toBe(a);
  });

  it('rejects malformed colour strings rather than emitting broken CSS', () => {
    expect(swatchHex({ id: 'x', colorHex: 'tomato' })).toMatch(/^#[0-9A-F]{6}$/);
    expect(swatchHex({ id: 'x', colorHex: '#12345' })).toMatch(/^#[0-9A-F]{6}$/);
  });

  it('resolvedColorHex: assignment → catalogue colour; missing item → stable fallback; nothing → null', () => {
    const catalog = new Map<string, MaterialItem>([
      [
        'exposed-brick',
        materialItemSchema.parse({ id: 'exposed-brick', name: 'Brick', colorHex: '#9C4A2F' }),
      ],
    ]);
    const rows = [
      assignment('material_A', 'external_wall', 'exposed-brick'),
      assignment('material_B', 'railing', 'gone-from-catalogue'),
    ];
    const ctx = { storeyId: null, elementId: null };
    expect(resolvedColorHex(rows, catalog, 'external_wall', ctx)).toBe('#9C4A2F');
    expect(resolvedColorHex(rows, catalog, 'railing', ctx)).toBe(
      fallbackColorFromId('gone-from-catalogue'),
    );
    expect(resolvedColorHex(rows, catalog, 'floor', ctx)).toBeNull();
  });
});
