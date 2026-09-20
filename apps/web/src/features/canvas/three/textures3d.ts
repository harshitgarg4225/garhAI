/**
 * textures3d.ts — materials that look like materials, with NO binary assets.
 *
 * The catalogue names a texture FAMILY per material (`tile`, `brick`,
 * `wood`, `stone`, `concrete`, `plaster`, `speckle`, `vein`, `metal`,
 * `glass` — `fixtures/catalog/materials.json`). Until now the 3D view drew
 * every one of them as a flat hex, so Kota stone, exposed brick and vitrified
 * tile differed only in tint.
 *
 * WHY PROCEDURAL, NOT VENDORED IMAGES. `scripts/check_web_assets.py` gates
 * asset URLs, the CSP ships `img-src 'self' data: blob:`, and a photo texture
 * needs a licence file beside it for every file (the Inter-font discipline).
 * A generated one needs none of that, ships zero bytes, and cannot 404 in a
 * client demo. The pattern functions here are PURE — `texturePixels` returns
 * an RGBA byte array with no canvas, no DOM and no GPU, so every pattern is
 * exercised in node by `textures3d.test.ts`; `getProceduralTexture` only
 * wraps that buffer in a `DataTexture`.
 *
 * SCALE. `geometryBuild` emits box-mapped UVs **in metres of surface**, so a
 * texture's physical size is one number here (`TEXTURE_TILE_MM`) turned into
 * `map.repeat`. A 600 mm floor tile is 600 mm on the floor whatever the room.
 *
 * `textureUrl` IS HONOURED (and fenced). A catalogue material may carry one;
 * the CSP allows same-origin and `data:` images only, so anything else falls
 * back to the procedural family rather than failing a request in the console
 * — `isLoadableTextureUrl` is that gate, and it is negative-tested.
 */

import {
  ClampToEdgeWrapping,
  DataTexture,
  RepeatWrapping,
  SRGBColorSpace,
  TextureLoader,
} from 'three';

import { hexToRgb } from '../facade/geometry3d';

// ---------------------------------------------------------------------------
// Families
// ---------------------------------------------------------------------------

/** The texture families `fixtures/catalog/materials.json` actually uses. */
export const TEXTURE_FAMILIES = [
  'tile',
  'brick',
  'wood',
  'stone',
  'concrete',
  'plaster',
  'speckle',
  'vein',
  'metal',
  'glass',
] as const;
export type TextureFamily = (typeof TEXTURE_FAMILIES)[number];

/** A catalogue string → a family this module can draw, or null. */
export function textureFamilyOf(texture: string | null | undefined): TextureFamily | null {
  if (texture === null || texture === undefined) return null;
  return (TEXTURE_FAMILIES as readonly string[]).includes(texture)
    ? (texture as TextureFamily)
    : null;
}

/**
 * How big one repeat of each family is on the real surface, mm. These are
 * the sizes an Indian spec sheet quotes: a 600 mm vitrified tile, a 230 mm
 * brick course, a 1200 mm laminate plank.
 */
export const TEXTURE_TILE_MM: Readonly<Record<TextureFamily, number>> = {
  tile: 600,
  brick: 900, // four 230 courses (the pattern draws 4 rows)
  wood: 1200,
  stone: 900,
  concrete: 2400,
  plaster: 1800,
  speckle: 600,
  vein: 1800,
  metal: 1200,
  glass: 2400,
};

/** Pixels per generated texture. 128² is ~64 kB and reads crisply at 1 m. */
export const TEXTURE_SIZE_PX = 128;

// ---------------------------------------------------------------------------
// The patterns — pure luminance fields in [0, 1], one per family
// ---------------------------------------------------------------------------

/** Deterministic hash noise in [0, 1). No Math.random: same input, same pixels. */
function hashNoise(x: number, y: number, salt: number): number {
  let h = Math.imul(x + 0x9e37, 0x85eb_ca6b) ^ Math.imul(y + 0x79b9, 0xc2b2_ae35);
  h = Math.imul(h ^ (h >>> 13), 0x27d4_eb2d + salt);
  return ((h ^ (h >>> 15)) >>> 0) / 0x1_0000_0000;
}

/** Smoothed noise: the average of a 3×3 hash neighbourhood. */
function softNoise(x: number, y: number, salt: number, size: number): number {
  let sum = 0;
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) {
      sum += hashNoise((x + dx + size) % size, (y + dy + size) % size, salt);
    }
  }
  return sum / 9;
}

/** Distance in pixels to the nearest line of a grid with `rows` bands. */
function gridEdge(v: number, size: number, bands: number): number {
  const band = size / bands;
  const inBand = v % band;
  return Math.min(inBand, band - inBand);
}

/**
 * Relative luminance of one texel of `family`, in roughly [0.7, 1.15] — a
 * MULTIPLIER on the material's colour, so every family works in every hue
 * and no texture ever paints its own colour over the architect's choice.
 */
export function texelShade(family: TextureFamily, x: number, y: number, size: number): number {
  const n = softNoise(x, y, 1, size);
  switch (family) {
    case 'tile': {
      // Four tiles across, grout in between, a faint sheen per tile.
      const g = Math.min(gridEdge(x, size, 4), gridEdge(y, size, 4));
      if (g < size / 128) return 0.78; // grout line
      return 1.0 + 0.03 * (n - 0.5);
    }
    case 'brick': {
      const rows = 4;
      const rowH = size / rows;
      const row = Math.floor(y / rowH);
      const offset = row % 2 === 0 ? 0 : size / 4; // running bond
      const g = Math.min(gridEdge(y, size, rows), gridEdge((x + offset) % size, size, 2) * 1.0);
      if (g < size / 96) return 0.82; // mortar
      return 0.95 + 0.12 * hashNoise(row, Math.floor((x + offset) / (size / 2)), 7);
    }
    case 'wood': {
      // Grain: bands along y, warped by low-frequency noise.
      const warp = softNoise(Math.floor(x / 8), Math.floor(y / 24), 3, size);
      const grain = Math.sin((x / size) * Math.PI * 14 + warp * 6);
      return 1.0 + 0.09 * grain + 0.03 * (n - 0.5);
    }
    case 'stone': {
      // Irregular blocks with a chipped edge.
      const cell = softNoise(Math.floor(x / 16), Math.floor(y / 16), 11, size);
      const g = Math.min(gridEdge(x, size, 8), gridEdge(y, size, 8));
      return (g < size / 96 ? 0.86 : 1.0) + 0.1 * (cell - 0.5);
    }
    case 'concrete':
      return 1.0 + 0.05 * (n - 0.5) + 0.02 * (hashNoise(x, y, 5) - 0.5);
    case 'plaster':
      return 1.0 + 0.025 * (n - 0.5);
    case 'speckle': {
      const s = hashNoise(x, y, 13);
      if (s > 0.94) return 0.72; // dark fleck
      if (s < 0.04) return 1.14; // bright fleck
      return 1.0 + 0.02 * (n - 0.5);
    }
    case 'vein': {
      const warp = softNoise(Math.floor(x / 12), Math.floor(y / 12), 17, size);
      const v = Math.abs(Math.sin((x + y) / size + warp * 2.4));
      return v < 0.06 ? 0.86 : 1.0 + 0.02 * (n - 0.5);
    }
    case 'metal':
      // Brushed: streaks along x, nearly no variation along y.
      return 1.0 + 0.06 * (hashNoise(0, y, 19) - 0.5) + 0.01 * (n - 0.5);
    case 'glass':
      return 1.0 + 0.01 * (n - 0.5);
  }
}

/**
 * One texture's RGBA bytes: the family's shade field multiplied into `hex`.
 * Pure — the whole reason the patterns are testable without a browser.
 */
export function texturePixels(
  family: TextureFamily,
  hex: string,
  size: number = TEXTURE_SIZE_PX,
): Uint8Array {
  const [r, g, b] = hexToRgb(hex);
  const data = new Uint8Array(size * size * 4);
  let i = 0;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const shade = texelShade(family, x, y, size);
      data[i] = Math.max(0, Math.min(255, Math.round(r * 255 * shade)));
      data[i + 1] = Math.max(0, Math.min(255, Math.round(g * 255 * shade)));
      data[i + 2] = Math.max(0, Math.min(255, Math.round(b * 255 * shade)));
      data[i + 3] = 255;
      i += 4;
    }
  }
  return data;
}

// ---------------------------------------------------------------------------
// Textures (cached — one DataTexture per family × colour)
// ---------------------------------------------------------------------------

const cache = new Map<string, DataTexture>();

/** The shared `DataTexture` for a family in a colour. Repeats are the caller's. */
export function getProceduralTexture(family: TextureFamily, hex: string): DataTexture {
  const key = `${family}|${hex}`;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  const texture = new DataTexture(texturePixels(family, hex), TEXTURE_SIZE_PX, TEXTURE_SIZE_PX);
  texture.colorSpace = SRGBColorSpace;
  texture.wrapS = RepeatWrapping;
  texture.wrapT = RepeatWrapping;
  texture.needsUpdate = true;
  cache.set(key, texture);
  return texture;
}

/**
 * May the page load this `textureUrl`? The CSP ships
 * `img-src 'self' data: blob:`, so a cross-origin URL is refused by the
 * browser — better to fall back to the procedural family than to put a
 * blocked request and a black mesh in front of an architect.
 */
export function isLoadableTextureUrl(url: string | null | undefined): boolean {
  if (url === null || url === undefined || url === '') return false;
  if (url.startsWith('data:image/')) return true;
  if (url.startsWith('blob:')) return true;
  // Same-origin absolute path. A protocol-relative or absolute URL is not.
  return url.startsWith('/') && !url.startsWith('//');
}

const urlCache = new Map<string, ReturnType<TextureLoader['load']>>();

/** The texture for a catalogue `textureUrl`, or null when it is not loadable. */
export function getUrlTexture(
  url: string | null | undefined,
): ReturnType<TextureLoader['load']> | null {
  if (!isLoadableTextureUrl(url) || url === null || url === undefined) return null;
  const hit = urlCache.get(url);
  if (hit !== undefined) return hit;
  const texture = new TextureLoader().load(url);
  texture.colorSpace = SRGBColorSpace;
  texture.wrapS = RepeatWrapping;
  texture.wrapT = RepeatWrapping;
  urlCache.set(url, texture);
  return texture;
}

/** Dispose every cached texture. Tests and full canvas teardown only. */
export function disposeTextures(): void {
  for (const texture of cache.values()) texture.dispose();
  cache.clear();
  for (const texture of urlCache.values()) texture.dispose();
  urlCache.clear();
}

/** Wrap mode for a texture that must NOT repeat (a one-off url map). */
export const NO_REPEAT_WRAP = ClampToEdgeWrapping;
