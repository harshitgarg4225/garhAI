/**
 * kits.ts — the two launch kits (§8), as typed data.
 *
 * THIS IS A MIRROR, NOT A SOURCE. The catalogue of record is
 * `fixtures/catalog/facade-kits.json` — the file the seed validates and
 * `GET /catalog/facade-kits` serves. The generator needs the full kit shape
 * (components + rules + colorway hexes) synchronously and client-side, and the
 * API client's zod schema deliberately strips everything but the card fields,
 * so the definitions are duplicated here **and pinned**:
 * `kits.fixture.test.ts` deep-compares this module against the fixture JSON,
 * so a divergence is a failing test, not a drift.
 *
 * Nothing here is a colour *asset* — the hexes below are data applied to
 * procedural materials (inherited fact 4: no textures, no HDRIs, no binaries).
 */

import type { FacadeKitDef, KitColorway } from './types';

export const CONTEMPORARY_KIT: FacadeKitDef = {
  id: 'contemporary',
  name: 'Contemporary',
  description:
    'Flat chajjas, a full-height cladding band at the stair bay, slim MS railings — monochrome with a wood accent.',
  components: {
    windowTrim: { style: 'flush-band', widthMm: 100, projectionMm: 40 },
    chajja: {
      style: 'flat',
      projectionMm: 600,
      thicknessMm: 100,
      allowedProjectionsMm: [600, 750],
    },
    parapetProfile: { style: 'banded', heightMm: 1050, capThicknessMm: 75 },
    claddingZones: {
      rule: 'stack full-height at entry bay',
      materialId: 'wpc-cladding',
      widthMm: 1200,
    },
    porch: { style: 'cantilever', projectionMm: 1800, thicknessMm: 200 },
    railing: { style: 'ms-slim', heightMm: 1050, materialId: 'ms-railing' },
  },
  colorways: [
    { id: 'mono-wood', name: 'Monochrome + wood', base: '#F2F0EB', accent: '#7A5230', trim: '#2E2E2E' },
    { id: 'warm-grey', name: 'Warm grey', base: '#DAD5CC', accent: '#8B6A45', trim: '#3C3C3C' },
  ],
  rules: {
    minFacadeWidthMm: 4500,
    chajjaOverOpenings: ['window', 'door'],
    claddingBayPickedBy: 'stair-adjacent external wall',
  },
};

export const MODERN_MINIMAL_KIT: FacadeKitDef = {
  id: 'modern-minimal',
  name: 'Modern Minimal',
  description:
    'Recessed windows with a hidden chajja, a plain parapet and a glass railing — white and grey.',
  components: {
    windowTrim: { style: 'recessed', widthMm: 0, projectionMm: -75 },
    chajja: {
      style: 'hidden',
      projectionMm: 600,
      thicknessMm: 75,
      allowedProjectionsMm: [600],
    },
    parapetProfile: { style: 'plain', heightMm: 1050, capThicknessMm: 50 },
    claddingZones: { rule: 'none', materialId: null, widthMm: 0 },
    porch: { style: 'flush', projectionMm: 1200, thicknessMm: 150 },
    railing: { style: 'glass', heightMm: 1050, materialId: 'glass-railing' },
  },
  colorways: [
    { id: 'white-grey', name: 'White + grey', base: '#FFFFFF', accent: '#8E8E8E', trim: '#6B6B6B' },
    { id: 'off-white', name: 'Off white', base: '#F5F3EE', accent: '#A8A8A3', trim: '#3C3C3C' },
  ],
  rules: {
    minFacadeWidthMm: 4500,
    chajjaOverOpenings: ['window'],
    recessDepthMm: 150,
  },
};

/** The launch kits, in catalogue order. */
export const FACADE_KITS: readonly FacadeKitDef[] = [CONTEMPORARY_KIT, MODERN_MINIMAL_KIT];

/** Kit by id, or null — an unknown id is a state, not an exception. */
export function kitById(kitId: string | null): FacadeKitDef | null {
  if (kitId === null) return null;
  return FACADE_KITS.find((k) => k.id === kitId) ?? null;
}

/** Colorway by id within a kit; defaults to the kit's first colorway. */
export function colorwayById(kit: FacadeKitDef, colorwayId: string | null): KitColorway {
  const fallback = kit.colorways[0];
  if (fallback === undefined) {
    // Every seeded kit has ≥1 colorway; a kit without one is a broken fixture.
    throw new Error(`Facade kit "${kit.id}" defines no colorways`);
  }
  if (colorwayId === null) return fallback;
  return kit.colorways.find((c) => c.id === colorwayId) ?? fallback;
}
