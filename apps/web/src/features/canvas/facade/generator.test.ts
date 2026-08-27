/**
 * generator.test.ts — the four guarantees the phase brief names:
 *
 *  1. DETERMINISM — same (model, kit, seed) ⇒ byte-identical components,
 *     including ids; different seed ⇒ the rule-allowed variants actually vary.
 *  2. TAGGING — every emitted spec is a well-formed `facadecomp` element with
 *     a legal kind and anchors that exist in the model it was generated from.
 *  3. EDIT — `facade.edit_component` applies an RFC 7386 patch to exactly one
 *     component, and its fold-written inverse restores the original.
 *  4. ISOLATION — folding `facade.apply_kit` leaves walls, rooms, openings,
 *     stairs, slabs, balconies and levels DEEP-EQUAL. The facade cannot dirty
 *     the plan.
 *
 * Ops fold through the REAL model core (`applyGroup`), not a stand-in.
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  canonicalJson,
  FACADE_COMPONENT_KINDS,
  FIXTURE_IDS,
  makeTwoRoomPlanWithOpenings,
  tryParseId,
  type FacadeComponentId,
  type Op,
  type ProjectDoc,
} from '@garh/model';

import { generateFacadeComponents, kitFitIssues } from './generator';
import { applyKitOp, clearFacadeOp, editComponentOp } from './ops';
import { CONTEMPORARY_KIT, MODERN_MINIMAL_KIT } from './kits';

/** A plan with external walls, a door and a window — the smallest real house. */
function docWithOpenings(): ProjectDoc {
  return makeTwoRoomPlanWithOpenings();
}

describe('generator determinism', () => {
  it('same (model, kit, seed) produces identical components, ids included', () => {
    const doc = docWithOpenings();
    for (const kit of [CONTEMPORARY_KIT, MODERN_MINIMAL_KIT]) {
      for (const seed of [0, 7, 123456]) {
        const a = generateFacadeComponents(doc.house, kit, seed);
        const b = generateFacadeComponents(doc.house, kit, seed);
        expect(canonicalJson(a)).toBe(canonicalJson(b));
      }
    }
  });

  it('the seed picks among rule-allowed chajja projections, deterministically', () => {
    const doc = docWithOpenings();
    const allowed = CONTEMPORARY_KIT.components.chajja.allowedProjectionsMm;
    const seen = new Set<number>();
    for (let seed = 0; seed < 32; seed += 1) {
      const comps = generateFacadeComponents(doc.house, CONTEMPORARY_KIT, seed);
      const chajja = comps.find((c) => c.kind === 'chajja');
      expect(chajja).toBeDefined();
      const projection = chajja?.params.projectionMm;
      expect(typeof projection).toBe('number');
      expect(allowed).toContain(projection);
      if (typeof projection === 'number') seen.add(projection);
    }
    // Both rule-allowed variants are reachable across seeds — the control is
    // honest, not decorative.
    expect(seen.size).toBe(allowed.length);
  });

  it('modern-minimal has one allowed projection, so every seed agrees', () => {
    const doc = docWithOpenings();
    for (const seed of [1, 2, 99]) {
      const comps = generateFacadeComponents(doc.house, MODERN_MINIMAL_KIT, seed);
      for (const c of comps) {
        if (c.kind !== 'chajja') continue;
        expect(c.params.projectionMm).toBe(600);
      }
    }
  });

  it('an explicit colorway wins; without one the seed picks deterministically', () => {
    const doc = docWithOpenings();
    const explicit = generateFacadeComponents(doc.house, CONTEMPORARY_KIT, 5, {
      colorwayId: 'warm-grey',
    });
    const cladding = explicit.find((c) => c.kind === 'cladding_zone');
    expect(cladding?.params.colorHex).toBe('#8B6A45'); // warm-grey accent

    const a = generateFacadeComponents(doc.house, CONTEMPORARY_KIT, 5);
    const b = generateFacadeComponents(doc.house, CONTEMPORARY_KIT, 5);
    expect(canonicalJson(a)).toBe(canonicalJson(b));
  });

  it('an empty model generates an empty facade and a teaching blocker', () => {
    const empty = applyGroup(docWithOpenings(), [clearFacadeOp()]).model; // facade cleared
    const bare = { ...empty.house, storeys: [], walls: [], openings: [] };
    expect(generateFacadeComponents(bare, CONTEMPORARY_KIT, 1)).toEqual([]);
    const issues = kitFitIssues(bare, CONTEMPORARY_KIT);
    expect(issues.some((i) => i.severity === 'blocker')).toBe(true);
  });
});

describe('component tagging', () => {
  it('every component id parses as facadecomp; kinds and anchors are real', () => {
    const doc = docWithOpenings();
    for (const kit of [CONTEMPORARY_KIT, MODERN_MINIMAL_KIT]) {
      const comps = generateFacadeComponents(doc.house, kit, 11);
      expect(comps.length).toBeGreaterThan(0);
      const wallIds = new Set(doc.house.walls.map((w) => w.id));
      const openingIds = new Set(doc.house.openings.map((o) => o.id));
      const storeyIds = new Set(doc.house.storeys.map((s) => s.id));
      const ids = new Set<string>();
      for (const c of comps) {
        expect(tryParseId(c.id)?.type).toBe('facadecomp');
        expect(ids.has(c.id)).toBe(false); // unique
        ids.add(c.id);
        expect(FACADE_COMPONENT_KINDS).toContain(c.kind);
        if (c.wallId !== null && c.wallId !== undefined) expect(wallIds.has(c.wallId)).toBe(true);
        if (c.openingId !== null && c.openingId !== undefined) {
          expect(openingIds.has(c.openingId)).toBe(true);
        }
        if (c.storeyId !== null && c.storeyId !== undefined) {
          expect(storeyIds.has(c.storeyId)).toBe(true);
        }
      }
    }
  });

  it('the entry door gets a porch and no chajja; windows get trims', () => {
    const doc = docWithOpenings();
    const comps = generateFacadeComponents(doc.house, CONTEMPORARY_KIT, 3);
    const porch = comps.filter((c) => c.kind === 'porch');
    expect(porch).toHaveLength(1);
    expect(porch[0]?.openingId).toBe(FIXTURE_IDS.doorMain);
    // door is in chajjaOverOpenings for contemporary, but the porch replaces it
    expect(comps.some((c) => c.kind === 'chajja' && c.openingId === FIXTURE_IDS.doorMain)).toBe(
      false,
    );
    expect(comps.some((c) => c.kind === 'window_trim')).toBe(true);
    expect(comps.some((c) => c.kind === 'parapet_profile')).toBe(true);
  });
});

describe('facade.edit_component', () => {
  it('applies a merge patch to one component and only that component', () => {
    const doc = docWithOpenings();
    const applied = applyGroup(doc, [
      applyKitOp(doc.house, CONTEMPORARY_KIT, 7, 'mono-wood'),
    ]).model;
    const chajja = applied.house.facade.components.find((c) => c.kind === 'chajja');
    expect(chajja).toBeDefined();
    if (chajja === undefined) return;

    const before = applied.house.facade.components;
    const edited = applyGroup(applied, [editComponentOp(chajja.id, { projectionMm: 750 })]).model;

    const after = edited.house.facade.components;
    expect(after).toHaveLength(before.length);
    for (const c of after) {
      const prev = before.find((b) => b.id === c.id);
      expect(prev).toBeDefined();
      if (c.id === chajja.id) {
        expect(c.params.projectionMm).toBe(750);
        // untouched keys survive the merge
        expect(c.params.thicknessMm).toBe(chajja.params.thicknessMm);
      } else {
        expect(canonicalJson(c)).toBe(canonicalJson(prev));
      }
    }
  });

  it('undo (the fold-written inverse) restores the original params', () => {
    const doc = docWithOpenings();
    const applied = applyGroup(doc, [applyKitOp(doc.house, CONTEMPORARY_KIT, 7, null)]).model;
    const chajja = applied.house.facade.components.find((c) => c.kind === 'chajja');
    if (chajja === undefined) throw new Error('fixture must generate a chajja');

    const forward = applyGroup(applied, [
      editComponentOp(chajja.id, { projectionMm: 750, sideOverhangMm: null }),
    ]);
    const undone = applyGroup(forward.model, forward.inverse).model;
    expect(canonicalJson(undone.house.facade)).toBe(canonicalJson(applied.house.facade));
  });

  it('rejects a float in the patch before it can reach the op log', () => {
    const id = 'facadecomp_0000000000000000000000000A' as FacadeComponentId;
    expect(() => editComponentOp(id, { projectionMm: 612.5 })).toThrow();
  });
});

describe('the isolation invariant (§8)', () => {
  it('applying a kit leaves every non-facade slice of the house deep-equal', () => {
    const doc = docWithOpenings();
    const op: Op = applyKitOp(doc.house, CONTEMPORARY_KIT, 42, 'mono-wood');
    const after = applyGroup(doc, [op]).model;

    const strip = (d: ProjectDoc): unknown => ({
      plot: d.plot,
      brief: d.brief,
      annotations: d.annotations,
      house: { ...d.house, facade: null },
    });
    expect(canonicalJson(strip(after))).toBe(canonicalJson(strip(doc)));

    // …and the facade itself is exactly the op's payload.
    expect(after.house.facade.kitId).toBe('contemporary');
    expect(after.house.facade.seed).toBe(42);
    expect(after.house.facade.components.length).toBeGreaterThan(0);
  });

  it('generator output references no wall/room id as its own', () => {
    const doc = docWithOpenings();
    const comps = generateFacadeComponents(doc.house, MODERN_MINIMAL_KIT, 9);
    const modelIds = new Set<string>([
      ...doc.house.walls.map((w) => w.id),
      ...doc.house.rooms.map((r) => r.id),
      ...doc.house.openings.map((o) => o.id),
    ]);
    for (const c of comps) expect(modelIds.has(c.id)).toBe(false);
  });

  it('clearing the facade is also isolation-clean', () => {
    const doc = docWithOpenings();
    const applied = applyGroup(doc, [applyKitOp(doc.house, MODERN_MINIMAL_KIT, 3, null)]).model;
    const cleared = applyGroup(applied, [clearFacadeOp()]).model;
    expect(cleared.house.facade.components).toEqual([]);
    expect(canonicalJson(cleared.house.walls)).toBe(canonicalJson(doc.house.walls));
    expect(canonicalJson(cleared.house.rooms)).toBe(canonicalJson(doc.house.rooms));
  });
});
