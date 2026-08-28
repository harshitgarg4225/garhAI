/**
 * resolve.test.ts — the ladder (override → material → surface default), the
 * override store, and the plan the sheets request will carry.
 *
 * The material rows here are real `MaterialAssignment` shapes resolved through
 * the REAL `resolveAssignment` from `features/canvas/materials`, not a local
 * re-implementation — the point of importing that resolver is that the section
 * and the 3D view cannot disagree about which material is on a wall, and a
 * spec that mocked it would prove nothing about that.
 */

import type { MaterialAssignment, MaterialAssignmentId, SurfaceGroupRef } from '@garh/model';
import { beforeEach, describe, expect, it } from 'vitest';

import type { MaterialItem } from '../../lib/schemas';
import { hatchPlan } from './plan';
import {
  SURFACE_DEFAULTS,
  hatchTargetKey,
  resolveHatch,
  resolveOverride,
  type HatchOverrides,
} from './resolve';
import { useHatchOverrideStore } from './store';
import { HATCH_PATTERN_KEYS, isHatchPatternKey } from './patterns';
import { SURFACE_GROUPS } from '@garh/model';

const STOREY_A = 'stry_01J0000000000000000000AA';
const STOREY_B = 'stry_01J0000000000000000000BB';
const WALL_1 = 'wall_01J000000000000000000001';

function assignment(
  materialId: string,
  target: Partial<SurfaceGroupRef> & Pick<SurfaceGroupRef, 'group'>,
): MaterialAssignment {
  const full: SurfaceGroupRef = {
    group: target.group,
    storeyId: target.storeyId ?? null,
    elementId: target.elementId ?? null,
  };
  return {
    id: `mat_${hatchTargetKey(full)}` as MaterialAssignmentId,
    target: full,
    materialId,
  };
}

function item(id: string, name: string, category: string): MaterialItem {
  return { id, name, category, colorHex: null, textureUrl: null, surfaceGroups: [] };
}

const CATALOG: ReadonlyMap<string, MaterialItem> = new Map([
  ['exposed-brick', item('exposed-brick', 'Exposed brick', 'wall')],
  ['exposed-concrete', item('exposed-concrete', 'Exposed concrete', 'wall')],
  ['teak-door', item('teak-door', 'Teak wood door', 'joinery')],
  ['kota-stone', item('kota-stone', 'Kota stone', 'floor')],
]);

const NO_OVERRIDES: HatchOverrides = new Map();
const overrideMap = (...entries: readonly (readonly [SurfaceGroupRef, string])[]): HatchOverrides =>
  new Map(
    entries.map(([target, pattern]) => {
      if (!isHatchPatternKey(pattern)) throw new Error(`${pattern} is not a pattern`);
      return [hatchTargetKey(target), { target, pattern }];
    }),
  );

describe('the surface defaults', () => {
  it('cover every surface group the model has, with real patterns', () => {
    // The "83 rules went inert" shape of bug: a default keyed on a value that
    // is not a member of the enum it is compared against fires never.
    for (const group of SURFACE_GROUPS) {
      const pattern = SURFACE_DEFAULTS[group];
      expect(pattern, `no default hatch for ${group}`).toBeDefined();
      expect(isHatchPatternKey(pattern), `${group} → ${pattern}`).toBe(true);
    }
    expect(Object.keys(SURFACE_DEFAULTS).sort()).toEqual([...SURFACE_GROUPS].sort());
  });

  it('says what the drawings service already draws for walls and slabs', () => {
    // projection/walls.py PATTERN_MASONRY = ANSI31 = diagonal.
    expect(SURFACE_DEFAULTS.external_wall).toBe('diagonal');
    expect(SURFACE_DEFAULTS.internal_wall).toBe('diagonal');
    // sections/project.py PATTERN_CONCRETE = ANSI37 = cross.
    expect(SURFACE_DEFAULTS.floor).toBe('cross');
    expect(SURFACE_DEFAULTS.roof).toBe('cross');
  });
});

describe('resolveHatch', () => {
  const ctx = { storeyId: null, elementId: null };

  it('falls back to the surface default with no material and no override', () => {
    const resolved = resolveHatch({
      materials: [],
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
      group: 'external_wall',
      ctx,
    });
    expect(resolved).toMatchObject({
      pattern: 'diagonal',
      source: 'surface-default',
      materialId: null,
    });
  });

  it('takes the hatch the assigned material implies', () => {
    const resolved = resolveHatch({
      materials: [assignment('exposed-brick', { group: 'external_wall' })],
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
      group: 'external_wall',
      ctx,
    });
    expect(resolved.pattern).toBe('brick');
    expect(resolved.source).toBe('material');
    expect(resolved.materialId).toBe('exposed-brick');
    expect(resolved.why).toContain('Exposed brick');
  });

  it('binds each material family the way a section needs it', () => {
    const check = (materialId: string, group: 'external_wall' | 'floor' | 'door'): string =>
      resolveHatch({
        materials: [assignment(materialId, { group })],
        catalog: CATALOG,
        overrides: NO_OVERRIDES,
        group,
        ctx,
      }).pattern;
    expect(check('exposed-brick', 'external_wall')).toBe('brick');
    expect(check('exposed-concrete', 'external_wall')).toBe('concrete');
    expect(check('teak-door', 'door')).toBe('timber');
    expect(check('kota-stone', 'floor')).toBe('stone');
  });

  it('still binds a material the catalogue no longer carries, from its id', () => {
    const resolved = resolveHatch({
      materials: [assignment('exposed-brick', { group: 'external_wall' })],
      catalog: new Map(),
      overrides: NO_OVERRIDES,
      group: 'external_wall',
      ctx,
    });
    // Dropping to the surface default here would silently un-brick a wall
    // whose material row is perfectly good.
    expect(resolved.pattern).toBe('brick');
    expect(resolved.source).toBe('material');
  });

  it('lets the architect override the material', () => {
    const target: SurfaceGroupRef = { group: 'external_wall', storeyId: null, elementId: null };
    const resolved = resolveHatch({
      materials: [assignment('exposed-brick', { group: 'external_wall' })],
      catalog: CATALOG,
      overrides: overrideMap([target, 'stone']),
      group: 'external_wall',
      ctx,
    });
    expect(resolved.pattern).toBe('stone');
    expect(resolved.source).toBe('override');
    // The material is still reported — the UI shows what is being overridden.
    expect(resolved.materialId).toBe('exposed-brick');
  });

  it('follows the same specificity ladder as a material assignment', () => {
    const materials = [
      assignment('exposed-brick', { group: 'external_wall' }),
      assignment('kota-stone', { group: 'external_wall', storeyId: STOREY_A }),
    ];
    const at = (storeyId: string | null): string =>
      resolveHatch({
        materials,
        catalog: CATALOG,
        overrides: NO_OVERRIDES,
        group: 'external_wall',
        ctx: { storeyId, elementId: null },
      }).pattern;
    expect(at(null)).toBe('brick');
    expect(at(STOREY_A)).toBe('stone');
    expect(at(STOREY_B)).toBe('brick');
  });

  it('narrows an override the same way: storey beats building, element beats both', () => {
    const overrides = overrideMap(
      [{ group: 'external_wall', storeyId: null, elementId: null }, 'plaster'],
      [{ group: 'external_wall', storeyId: STOREY_A, elementId: null }, 'stone'],
      [{ group: 'external_wall', storeyId: null, elementId: WALL_1 }, 'timber'],
    );
    const at = (storeyId: string | null, elementId: string | null): string | null =>
      resolveOverride(overrides, 'external_wall', { storeyId, elementId });
    expect(at(null, null)).toBe('plaster');
    expect(at(STOREY_A, null)).toBe('stone');
    expect(at(STOREY_B, null)).toBe('plaster');
    expect(at(STOREY_A, WALL_1)).toBe('timber');
    // A different group is untouched by any of it.
    expect(resolveOverride(overrides, 'floor', { storeyId: STOREY_A, elementId: null })).toBeNull();
  });
});

describe('the override store', () => {
  beforeEach(() => {
    useHatchOverrideStore.setState({ projectId: null, overrides: new Map() });
  });

  it('sets, reads back and clears one surface', () => {
    const target: SurfaceGroupRef = { group: 'floor', storeyId: null, elementId: null };
    useHatchOverrideStore.getState().setOverride(target, 'timber');
    expect(
      resolveOverride(useHatchOverrideStore.getState().overrides, 'floor', {
        storeyId: null,
        elementId: null,
      }),
    ).toBe('timber');

    useHatchOverrideStore.getState().clearOverride(target);
    expect(useHatchOverrideStore.getState().overrides.size).toBe(0);
  });

  it('publishes a new map on change and the same one on a no-op', () => {
    const target: SurfaceGroupRef = { group: 'floor', storeyId: null, elementId: null };
    const store = useHatchOverrideStore.getState();
    store.setOverride(target, 'timber');
    const first = useHatchOverrideStore.getState().overrides;
    // A repeat of the same choice must not re-render every consumer.
    store.setOverride(target, 'timber');
    expect(useHatchOverrideStore.getState().overrides).toBe(first);
    store.setOverride(target, 'stone');
    expect(useHatchOverrideStore.getState().overrides).not.toBe(first);
  });

  it('drops overrides when the project changes, and keeps them when it does not', () => {
    const target: SurfaceGroupRef = { group: 'floor', storeyId: null, elementId: null };
    const store = useHatchOverrideStore.getState();
    store.bindProject('proj_A');
    store.setOverride(target, 'timber');
    store.bindProject('proj_A');
    expect(useHatchOverrideStore.getState().overrides.size).toBe(1);
    store.bindProject('proj_B');
    expect(useHatchOverrideStore.getState().overrides.size).toBe(0);
  });

  it('carries the target on the value, so consumers never parse the key', () => {
    const target: SurfaceGroupRef = { group: 'roof', storeyId: STOREY_A, elementId: null };
    useHatchOverrideStore.getState().setOverride(target, 'steel');
    const entry = useHatchOverrideStore.getState().overrides.get(hatchTargetKey(target));
    expect(entry).toEqual({ target, pattern: 'steel' });
  });
});

describe('hatchPlan — what a sheet request would carry', () => {
  it('answers for every surface group, whatever the document says', () => {
    const plan = hatchPlan({ materials: [], catalog: CATALOG, overrides: NO_OVERRIDES });
    expect(plan).toHaveLength(SURFACE_GROUPS.length);
    for (const group of SURFACE_GROUPS) {
      const entry = plan.find((row) => row.group === group && row.storeyId === null);
      expect(entry?.pattern, group).toBe(SURFACE_DEFAULTS[group]);
      expect(entry?.source).toBe('surface-default');
    }
  });

  it('adds a row for every storey and element the document actually names', () => {
    const plan = hatchPlan({
      materials: [
        assignment('exposed-brick', { group: 'external_wall' }),
        assignment('kota-stone', { group: 'external_wall', storeyId: STOREY_A }),
      ],
      catalog: CATALOG,
      overrides: overrideMap([{ group: 'floor', storeyId: STOREY_B, elementId: null }, 'timber']),
    });
    const walls = plan.filter((row) => row.group === 'external_wall');
    expect(walls.map((row) => [row.storeyId, row.pattern])).toEqual([
      [null, 'brick'],
      [STOREY_A, 'stone'],
    ]);
    const floors = plan.filter((row) => row.group === 'floor');
    expect(floors.map((row) => [row.storeyId, row.pattern, row.source])).toEqual([
      [null, 'cross', 'surface-default'],
      [STOREY_B, 'timber', 'override'],
    ]);
    // No row invented for a storey nobody mentioned.
    expect(plan.some((row) => row.storeyId !== null && row.group === 'roof')).toBe(false);
  });

  it('is deterministic, and every pattern in it is a real one', () => {
    const input = {
      materials: [assignment('exposed-brick', { group: 'external_wall', elementId: WALL_1 })],
      catalog: CATALOG,
      overrides: NO_OVERRIDES,
    };
    expect(hatchPlan(input)).toEqual(hatchPlan(input));
    for (const entry of hatchPlan(input)) {
      expect(HATCH_PATTERN_KEYS as readonly string[]).toContain(entry.pattern);
    }
  });
});
