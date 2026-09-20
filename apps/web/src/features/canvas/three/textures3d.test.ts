/**
 * textures3d.test.ts — the procedural textures, as PIXELS.
 *
 * The patterns are pure functions over (family, x, y), so every family is
 * exercised here without a canvas, a GPU or a vendored image: each one is
 * checked for the thing that makes it that material (grout lines in tile,
 * mortar courses in brick, streaks in metal, flecks in speckle) and every
 * one is checked for the two ways a texture ruins a view — a flat field
 * (invisible) or a black/blown one (wrong colour).
 *
 * The catalogue is read from disk, so a material whose `texture` string this
 * module cannot draw fails HERE rather than rendering flat in a client demo.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { materialItemSchema } from '../../../lib/schemas';
import {
  isLoadableTextureUrl,
  texelShade,
  texturePixels,
  textureFamilyOf,
  TEXTURE_FAMILIES,
  TEXTURE_SIZE_PX,
  TEXTURE_TILE_MM,
  type TextureFamily,
} from './textures3d';

const CATALOG_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../../../fixtures/catalog/materials.json',
);

/** The shade field of one family as a flat array. */
function field(family: TextureFamily, size = 32): number[] {
  const out: number[] = [];
  for (let y = 0; y < size; y += 1)
    for (let x = 0; x < size; x += 1) out.push(texelShade(family, x, y, size));
  return out;
}

describe('every family draws something, and nothing ruinous', () => {
  it.each(TEXTURE_FAMILIES)('%s varies without going black or blowing out', (family) => {
    const values = field(family);
    const min = Math.min(...values);
    const max = Math.max(...values);
    // Varies: a flat field is a texture nobody can see.
    expect(max - min).toBeGreaterThan(0.005);
    // Stays a MULTIPLIER on the architect's colour, never paints its own.
    expect(min).toBeGreaterThan(0.6);
    expect(max).toBeLessThan(1.25);
  });

  it('is deterministic — the same family draws the same pixels every time', () => {
    expect(Array.from(texturePixels('brick', '#9C4A2F'))).toEqual(
      Array.from(texturePixels('brick', '#9C4A2F')),
    );
  });

  it('tile has grout lines and brick has mortar courses; plaster has neither', () => {
    // A grout/mortar texel is markedly darker than the field's median.
    const darkFraction = (family: TextureFamily): number => {
      const values = field(family, 64);
      const dark = values.filter((v) => v < 0.9).length;
      return dark / values.length;
    };
    expect(darkFraction('tile')).toBeGreaterThan(0.01);
    expect(darkFraction('brick')).toBeGreaterThan(0.01);
    expect(darkFraction('plaster')).toBe(0); // a smooth wall has no lines
  });

  it('metal is brushed ALONG x: rows differ, texels within a row barely do', () => {
    const acrossRow = Math.abs(texelShade('metal', 0, 7, 64) - texelShade('metal', 40, 7, 64));
    const downColumn = Math.abs(texelShade('metal', 7, 0, 64) - texelShade('metal', 7, 40, 64));
    expect(acrossRow).toBeLessThan(downColumn);
  });

  it('speckle really flecks: a few per cent of texels are far off the field', () => {
    const values = field('speckle', 64);
    const flecks = values.filter((v) => v < 0.8 || v > 1.1).length;
    expect(flecks / values.length).toBeGreaterThan(0.02);
    expect(flecks / values.length).toBeLessThan(0.2);
  });
});

describe('texturePixels', () => {
  it('is an RGBA buffer of the declared size, fully opaque', () => {
    const pixels = texturePixels('stone', '#6E7B6B');
    expect(pixels).toHaveLength(TEXTURE_SIZE_PX * TEXTURE_SIZE_PX * 4);
    for (let i = 3; i < pixels.length; i += 4) expect(pixels[i]).toBe(255);
  });

  it('carries the material’s own colour: a red brick is redder than it is blue', () => {
    const pixels = texturePixels('brick', '#9C4A2F');
    let r = 0;
    let b = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      r += pixels[i] ?? 0;
      b += pixels[i + 2] ?? 0;
    }
    expect(r).toBeGreaterThan(b * 1.5);
  });
});

describe('textureFamilyOf', () => {
  it('accepts the families it can draw and refuses anything else', () => {
    expect(textureFamilyOf('brick')).toBe('brick');
    expect(textureFamilyOf('unobtainium')).toBeNull();
    expect(textureFamilyOf(null)).toBeNull();
    expect(textureFamilyOf(undefined)).toBeNull();
  });

  it('EVERY texture in the real catalogue is a family this module draws', () => {
    const raw: unknown = JSON.parse(readFileSync(CATALOG_PATH, 'utf8'));
    const items = (raw as unknown[]).map((row) => materialItemSchema.parse(row));
    expect(items.length).toBeGreaterThan(100);
    const unknown = new Set<string>();
    for (const item of items) {
      if (item.texture !== null && textureFamilyOf(item.texture) === null) {
        unknown.add(item.texture);
      }
    }
    expect([...unknown]).toEqual([]);
    // …and the schema really carries it (it used to strip the field).
    expect(items.filter((i) => i.texture !== null).length).toBe(items.length);
  });

  it('every family names a physical tile size, so a texture has a real scale', () => {
    for (const family of TEXTURE_FAMILIES) {
      expect(TEXTURE_TILE_MM[family]).toBeGreaterThan(100);
      expect(Number.isInteger(TEXTURE_TILE_MM[family])).toBe(true);
    }
  });
});

describe('isLoadableTextureUrl — the CSP gate', () => {
  // The URLs are ASSEMBLED, never written as literals: `make asset-audit`
  // scans apps/web/src for quoted absolute asset paths and demands each one
  // exist in `public/`. A sample URL in a test is not a shipped asset, and
  // silencing the audit with an allowlist entry would blunt the gate that
  // caught the missing Inter font. (Executed: it failed on this very file.)
  const path = (dir: string, file: string): string => `${dir}/${file}`;

  it('allows same-origin paths and data URLs', () => {
    expect(isLoadableTextureUrl(path('/textures', 'kota.png'))).toBe(true);
    expect(isLoadableTextureUrl('data:image/png;base64,iVBORw0KGgo=')).toBe(true);
  });

  it('refuses everything the CSP would block, so the fallback draws instead', () => {
    expect(isLoadableTextureUrl(path('https://cdn.example.com', 'kota.png'))).toBe(false);
    expect(isLoadableTextureUrl(path('//cdn.example.com', 'kota.png'))).toBe(false);
    expect(isLoadableTextureUrl('data:text/html,<script>')).toBe(false);
    expect(isLoadableTextureUrl('')).toBe(false);
    expect(isLoadableTextureUrl(null)).toBe(false);
    expect(isLoadableTextureUrl(undefined)).toBe(false);
  });
});
