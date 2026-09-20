/**
 * materials3d.test.ts — the mesh's material, end to end: which assignment
 * wins, which colour that is, and — since 2026-09-20 — which TEXTURE.
 *
 * The scorecard finding this answers: "Everything is a flat hex colour
 * (textureUrl ignored)". A material now carries a `map`, the map is shared
 * per family so a recolour is still a uniform write, and a `textureUrl` the
 * CSP would block falls back to the family rather than to a black mesh.
 */

import { describe, expect, it } from 'vitest';

import { MeshStandardMaterial } from 'three';

import type { MaterialAssignment } from '@garh/model';

import {
  colorForScope,
  disposeSolidMaterials,
  getSolidMaterial,
  textureForScope,
  type SurfaceTextureSpec,
} from './materials3d';
import { TEXTURE_TILE_MM } from './textures3d';

const BRICK: MaterialAssignment = {
  id: 'material_A',
  target: { group: 'external_wall', storeyId: null, elementId: null },
  materialId: 'exposed-brick',
};
const TEXTURES: Readonly<Record<string, SurfaceTextureSpec>> = {
  'exposed-brick': { family: 'brick', url: null },
  'vitrified-tile-600': { family: 'tile', url: null },
  'mystery-slab': { family: null, url: null },
  'cdn-stone': { family: 'stone', url: 'https://cdn.example.com/stone.png' },
};
const SCOPE = { surface: 'external_wall', storeyId: null, elementId: null } as const;

describe('textureForScope', () => {
  it('gives the assigned material’s texture', () => {
    expect(textureForScope([BRICK], SCOPE, TEXTURES)).toEqual({ family: 'brick', url: null });
  });

  it('is null with no assignment, no catalogue, or an unknown material', () => {
    expect(textureForScope([], SCOPE, TEXTURES)).toBeNull();
    expect(textureForScope([BRICK], SCOPE, undefined)).toBeNull();
    expect(
      textureForScope([{ ...BRICK, materialId: 'not-in-catalogue' }], SCOPE, TEXTURES),
    ).toBeNull();
  });

  it('follows the SAME specificity the colour does — element beats building', () => {
    const onThisWall: MaterialAssignment = {
      id: 'material_B',
      target: { group: 'external_wall', storeyId: null, elementId: 'wall_1' },
      materialId: 'vitrified-tile-600',
    };
    const scope = { surface: 'external_wall', storeyId: null, elementId: 'wall_1' } as const;
    expect(textureForScope([BRICK, onThisWall], scope, TEXTURES)?.family).toBe('tile');
    expect(
      colorForScope([BRICK, onThisWall], scope, { 'vitrified-tile-600': '#E8E4DC' }, null),
    ).toBe('#E8E4DC');
    // …and the un-scoped mesh keeps the building-wide one.
    expect(textureForScope([BRICK, onThisWall], SCOPE, TEXTURES)?.family).toBe('brick');
  });
});

describe('getSolidMaterial', () => {
  it('is still a flat-colour material when nothing declares a texture', () => {
    disposeSolidMaterials();
    const plain = getSolidMaterial('#E3DDD2', false);
    expect(plain).toBeInstanceOf(MeshStandardMaterial);
    expect(plain.map).toBeNull();
    expect(getSolidMaterial('#E3DDD2', false, { family: null, url: null }).map).toBeNull();
  });

  it('maps a family, at the family’s own physical tile size', () => {
    disposeSolidMaterials();
    const brick = getSolidMaterial('#9C4A2F', false, { family: 'brick', url: null });
    expect(brick.map).not.toBeNull();
    // UVs are metres, so repeat is tiles-per-metre.
    const perMetre = 1000 / TEXTURE_TILE_MM.brick;
    expect(brick.map?.repeat.x).toBeCloseTo(perMetre, 6);
    expect(brick.map?.repeat.y).toBeCloseTo(perMetre, 6);
    // The colour is still the architect's: the map is a white-based
    // multiplier, so one texture serves every colourway.
    expect(brick.color.getHexString().toUpperCase()).toBe('9C4A2F');
  });

  it('caches per (colour × glass × texture), so a recolour is a uniform write', () => {
    disposeSolidMaterials();
    const a = getSolidMaterial('#9C4A2F', false, { family: 'brick', url: null });
    const b = getSolidMaterial('#9C4A2F', false, { family: 'brick', url: null });
    expect(a).toBe(b);
    expect(getSolidMaterial('#9C4A2F', false, { family: 'tile', url: null })).not.toBe(a);
    expect(getSolidMaterial('#9C4A2F', false)).not.toBe(a);
    // Two colours of the same family share ONE texture object.
    const other = getSolidMaterial('#DED8CE', false, { family: 'brick', url: null });
    expect(other.map).toBe(a.map);
  });

  it('a blocked textureUrl falls back to the family, never to a black mesh', () => {
    disposeSolidMaterials();
    const blocked = getSolidMaterial('#6E7B6B', false, TEXTURES['cdn-stone'] ?? null);
    const family = getSolidMaterial('#6E7B6B', false, { family: 'stone', url: null });
    expect(blocked).toBe(family);
    expect(blocked.map).not.toBeNull();
  });

  it('glass stays translucent and un-depth-written, textured or not', () => {
    disposeSolidMaterials();
    const glass = getSolidMaterial('#CFE3E8', true, { family: 'glass', url: null });
    expect(glass.transparent).toBe(true);
    expect(glass.depthWrite).toBe(false);
    expect(glass.opacity).toBeLessThan(1);
  });
});
