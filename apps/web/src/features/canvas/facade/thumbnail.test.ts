/**
 * thumbnail.test.ts — kit-card previews are generator output, deterministic,
 * and never an asset. Pins:
 *
 *  - same (model, kit, seed, colorway) ⇒ identical spec (the SVG is a pure
 *    function of it);
 *  - the sample house always yields a drawable spec, so an empty project's
 *    cards are never blank;
 *  - the preview contains the kit's own hexes — the picture and the geometry
 *    share one colour source;
 *  - seeds that change the chajja projection change the picture (an honest
 *    control, §15), and the user's own model is preferred when it has a
 *    frontage.
 */

import { describe, expect, it } from 'vitest';

import { canonicalJson, makeTwoRoomPlanWithOpenings } from '@garh/model';

import { CONTEMPORARY_KIT, MODERN_MINIMAL_KIT } from './kits';
import { hasFrontage, kitThumbnailSpec, pickFrontage, sampleHouseForThumbnails } from './thumbnail';

describe('the sample house', () => {
  it('has a frontage and a stable identity', () => {
    const a = sampleHouseForThumbnails();
    const b = sampleHouseForThumbnails();
    expect(a).toBe(b); // cached — thumbnails never disagree about the model
    expect(hasFrontage(a)).toBe(true);
    expect(pickFrontage(a)).not.toBeNull();
  });
});

describe('kitThumbnailSpec', () => {
  it('is deterministic per (model, kit, seed, colorway)', () => {
    for (const kit of [CONTEMPORARY_KIT, MODERN_MINIMAL_KIT]) {
      const a = kitThumbnailSpec(null, kit, 7, null);
      const b = kitThumbnailSpec(null, kit, 7, null);
      expect(a).not.toBeNull();
      expect(canonicalJson(a)).toBe(canonicalJson(b));
    }
  });

  it('draws the colorway the kit data names — no second colour source', () => {
    const spec = kitThumbnailSpec(null, CONTEMPORARY_KIT, 7, 'mono-wood');
    expect(spec).not.toBeNull();
    if (spec === null) return;
    const fills = new Set(spec.rects.map((r) => r.fill));
    expect(fills.has('#F2F0EB')).toBe(true); // base — the wall face
    expect(fills.has('#7A5230')).toBe(true); // accent — cladding / porch
    expect(fills.has('#2E2E2E')).toBe(true); // trim — chajjas, parapet cap
  });

  it('the seed changes the picture when it is allowed to (honest control)', () => {
    // With no explicit colorway the seed picks one. FNV-1a('colorway#0') is
    // even and FNV-1a('colorway#1') is odd (verified against the reference
    // implementation), so seeds 0 and 1 paint different colorways — the card
    // visibly answers "what does the seed do?".
    const s0 = kitThumbnailSpec(null, CONTEMPORARY_KIT, 0, null);
    const s1 = kitThumbnailSpec(null, CONTEMPORARY_KIT, 1, null);
    expect(s0).not.toBeNull();
    expect(s1).not.toBeNull();
    expect(canonicalJson(s0)).not.toBe(canonicalJson(s1));
  });

  it('prefers the user model when it has a frontage', () => {
    const doc = makeTwoRoomPlanWithOpenings();
    const own = kitThumbnailSpec(doc.house, CONTEMPORARY_KIT, 7, 'mono-wood');
    const sample = kitThumbnailSpec(null, CONTEMPORARY_KIT, 7, 'mono-wood');
    expect(own).not.toBeNull();
    if (own === null || sample === null) return;
    // The two-room plan's frontage is 6 m; the sample's is 7.2 m.
    expect(own.widthMm).not.toBe(sample.widthMm);
  });

  it('rect count grows with the components on the frontage', () => {
    const spec = kitThumbnailSpec(null, MODERN_MINIMAL_KIT, 3, null);
    expect(spec).not.toBeNull();
    if (spec === null) return;
    // Backdrop + at least one opening + at least one facade box.
    expect(spec.rects.length).toBeGreaterThan(3);
    // Every rect is drawable.
    for (const r of spec.rects) {
      expect(r.w).toBeGreaterThan(0);
      expect(r.h).toBeGreaterThan(0);
      expect(r.fill).toMatch(/^#/);
    }
  });
});
