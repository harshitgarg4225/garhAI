/**
 * componentBoxes.test.ts — the render-geometry contract:
 *
 *  - boxes derive placement from the model, so they sit on the OUTSIDE face,
 *    at the elevations the model dictates (lintel top for a chajja, sill for
 *    a trim, terrace level for the parapet);
 *  - orphaned components (deleted anchor) produce zero boxes, never garbage;
 *  - op-28 patches change the produced geometry (the edit is real);
 *  - balcony railings stand on open edges only, never on the wall edge.
 *
 * All assertions run on `makeTwoRoomPlanWithOpenings` + the thumbnail sample
 * house — real folds, real derived rooms, no hand-mocked model.
 */

import { describe, expect, it } from 'vitest';

import {
  applyGroup,
  makeTwoRoomPlanWithOpenings,
  type FacadeComponent,
  type OpeningId,
} from '@garh/model';

import { balconyOpenEdges, boxesForComponent, externalCentroid, wallFrame } from './componentBoxes';
import { generateFacadeComponents } from './generator';
import { CONTEMPORARY_KIT, FACADE_KITS, MODERN_MINIMAL_KIT } from './kits';
import { applyKitOp, editComponentOp } from './ops';
import { sampleHouseForThumbnails } from './thumbnail';

function appliedDoc(kit = CONTEMPORARY_KIT, seed = 7) {
  const doc = makeTwoRoomPlanWithOpenings();
  return applyGroup(doc, [applyKitOp(doc.house, kit, seed, null)]).model;
}

function componentOf(house: ReturnType<typeof appliedDoc>['house'], kind: FacadeComponent['kind']) {
  const c = house.facade.components.find((x) => x.kind === kind);
  if (c === undefined) throw new Error(`expected a ${kind} component`);
  return c;
}

describe('wall frames', () => {
  it('outward normals point away from the footprint centroid', () => {
    const house = sampleHouseForThumbnails();
    const ground = house.storeys[0];
    if (ground === undefined) throw new Error('sample must have a ground storey');
    const inside = externalCentroid(house, ground.id);
    expect(inside).not.toBeNull();
    if (inside === null) return;
    for (const wall of house.walls) {
      if (wall.storeyId !== ground.id) continue;
      const frame = wallFrame(wall, inside);
      expect(frame).not.toBeNull();
      if (frame === null) continue;
      const mx = (wall.a.x + wall.b.x) / 2;
      const my = (wall.a.y + wall.b.y) / 2;
      // Walking outward from the wall midpoint must increase distance from
      // the centroid.
      const stepped = {
        x: mx + frame.outX * 100,
        y: my + frame.outY * 100,
      };
      const before = (mx - inside.x) ** 2 + (my - inside.y) ** 2;
      const after = (stepped.x - inside.x) ** 2 + (stepped.y - inside.y) ** 2;
      expect(after).toBeGreaterThan(before);
    }
  });
});

describe('chajja geometry', () => {
  it('sits at the opening head, outside the wall, at the kit projection', () => {
    const doc = appliedDoc();
    const house = doc.house;
    const chajja = componentOf(house, 'chajja');
    const boxes = boxesForComponent(house, chajja);
    expect(boxes).toHaveLength(1);
    const box = boxes[0];
    if (box === undefined) return;

    const opening = house.openings.find((o) => o.id === chajja.openingId);
    const wall = house.walls.find((w) => w.id === chajja.wallId);
    const storey = house.storeys.find((s) => s.id === chajja.storeyId);
    if (opening === undefined || wall === undefined || storey === undefined) {
      throw new Error('anchors must resolve');
    }
    // Elevation: lintel top = FFL + sill + opening height.
    expect(box.baseElevMm).toBe(storey.level.fflMm + opening.sillMm + opening.heightMm);
    // Depth is the (seed-picked) projection; length covers the opening.
    expect(CONTEMPORARY_KIT.components.chajja.allowedProjectionsMm).toContain(box.depthMm);
    expect(box.lenMm).toBeGreaterThanOrEqual(opening.widthMm);
    // Outside: the box centre is off the wall centreline on the outward side.
    const inside = externalCentroid(house, wall.storeyId);
    if (inside === null) throw new Error('centroid must exist');
    const frame = wallFrame(wall, inside);
    if (frame === null) throw new Error('frame must exist');
    const rel =
      (box.cx - (wall.a.x + wall.b.x) / 2) * frame.outX +
      (box.cy - (wall.a.y + wall.b.y) / 2) * frame.outY;
    expect(rel).toBeGreaterThan(0);
  });

  it('an op-28 projection patch changes the produced box', () => {
    const doc = appliedDoc();
    const chajja = componentOf(doc.house, 'chajja');
    const before = boxesForComponent(doc.house, chajja)[0];
    const edited = applyGroup(doc, [editComponentOp(chajja.id, { projectionMm: 750 })]).model;
    const after = boxesForComponent(edited.house, componentOf(edited.house, 'chajja'))[0];
    expect(before).toBeDefined();
    expect(after).toBeDefined();
    expect(after?.depthMm).toBe(750);
  });
});

describe('orphaned components', () => {
  it('produce zero boxes when their anchor is gone', () => {
    const doc = appliedDoc();
    const chajja = componentOf(doc.house, 'chajja');
    // Sever the anchor by hand — cheaper than a delete op and tests exactly
    // the render-time contract (render never throws on a stale facade).
    const orphan: FacadeComponent = {
      ...chajja,
      openingId: 'opening_0000000000000000000000000A' as OpeningId,
    };
    expect(boxesForComponent(doc.house, orphan)).toEqual([]);
    const noWall: FacadeComponent = { ...chajja, wallId: null };
    expect(boxesForComponent(doc.house, noWall)).toEqual([]);
  });
});

describe('railings on the sample balcony', () => {
  it('stand on open edges only and honour the style patch', () => {
    const house = sampleHouseForThumbnails();
    const balcony = house.balconies[0];
    if (balcony === undefined) throw new Error('sample must have a balcony');
    const open = balconyOpenEdges(house, balcony);
    // The 4th edge is glued to the south frontage wall.
    expect(open).toHaveLength(3);

    const comps = generateFacadeComponents(house, MODERN_MINIMAL_KIT, 3);
    const railing = comps.find((c) => c.kind === 'railing');
    expect(railing).toBeDefined();
    if (railing === undefined) return;
    const glassBoxes = boxesForComponent(house, {
      id: railing.id,
      kind: railing.kind,
      storeyId: railing.storeyId ?? null,
      wallId: railing.wallId ?? null,
      openingId: railing.openingId ?? null,
      params: railing.params,
    });
    // Glass: one panel + one rail per open edge.
    expect(glassBoxes).toHaveLength(open.length * 2);

    const msBoxes = boxesForComponent(house, {
      id: railing.id,
      kind: railing.kind,
      storeyId: railing.storeyId ?? null,
      wallId: railing.wallId ?? null,
      openingId: railing.openingId ?? null,
      params: { ...railing.params, style: 'ms-slim' },
    });
    // MS: rail + posts per edge — strictly more members than glass.
    expect(msBoxes.length).toBeGreaterThan(glassBoxes.length);
  });
});

describe('parapet and cladding derive building height from the model', () => {
  it('parapet cap tops out above the top storey', () => {
    const doc = appliedDoc();
    const house = doc.house;
    const parapet = componentOf(house, 'parapet_profile');
    const boxes = boxesForComponent(house, parapet);
    expect(boxes.length).toBeGreaterThan(0);
    const top = house.storeys[house.storeys.length - 1];
    if (top === undefined) throw new Error('storey must exist');
    const roofElev = top.level.fflMm + top.heightMm;
    for (const b of boxes) expect(b.baseElevMm).toBeGreaterThanOrEqual(roofElev);
  });

  it('cladding runs from grade to the parapet line', () => {
    const doc = appliedDoc();
    const house = doc.house;
    const cladding = componentOf(house, 'cladding_zone');
    const boxes = boxesForComponent(house, cladding);
    expect(boxes).toHaveLength(1);
    const box = boxes[0];
    if (box === undefined) return;
    expect(box.baseElevMm).toBe(0);
    const top = house.storeys[house.storeys.length - 1];
    if (top === undefined) throw new Error('storey must exist');
    expect(box.heightMm).toBe(top.level.fflMm + top.heightMm + house.levels.parapetMm);
  });
});

// ---------------------------------------------------------------------------
// Every kit in the catalogue must actually build something
// ---------------------------------------------------------------------------

describe('every launch kit draws real geometry', () => {
  // A kit is data: it can be added to the catalogue, validate, seed, show a
  // card, apply — and then produce nothing, or components whose style string
  // no builder implements. Nothing else in this suite would notice, because
  // every spec above names `contemporary` explicitly.
  it.each(FACADE_KITS.map((kit) => [kit.id, kit] as const))(
    '%s instantiates components and every one of them extrudes',
    (_id, kit) => {
      const doc = makeTwoRoomPlanWithOpenings();
      const applied = applyGroup(doc, [applyKitOp(doc.house, kit, 7, null)]).model;
      const components = applied.house.facade.components;
      expect(components.length).toBeGreaterThan(0);

      let total = 0;
      for (const component of components) {
        const boxes = boxesForComponent(applied.house, component);
        // Every kind a launch kit emits is one `boxesForComponent` draws.
        expect(boxes.length, `${kit.id}: ${component.kind} drew nothing`).toBeGreaterThan(0);
        for (const box of boxes) {
          expect(Number.isFinite(box.cx) && Number.isFinite(box.cy)).toBe(true);
          expect(box.lenMm).toBeGreaterThan(0);
          expect(box.depthMm).toBeGreaterThan(0);
          expect(box.heightMm).toBeGreaterThan(0);
          expect(box.colorHex).toMatch(/^#[0-9A-Fa-f]{6}$/);
        }
        total += boxes.length;
      }
      expect(total).toBeGreaterThan(4);
    },
  );

  it('the four kits are genuinely different facades, not four colourways', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const signatures = FACADE_KITS.map((kit) => {
      const applied = applyGroup(doc, [applyKitOp(doc.house, kit, 7, null)]).model;
      return JSON.stringify(
        applied.house.facade.components
          .map((c) => boxesForComponent(applied.house, c))
          .flat()
          .map((b) => [b.lenMm, b.depthMm, b.heightMm, b.baseElevMm, b.colorHex]),
      );
    });
    expect(new Set(signatures).size).toBe(FACADE_KITS.length);
  });
});
