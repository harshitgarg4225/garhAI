/**
 * The pure parts of the renders feature: the pack/preset mirror contract and
 * the hand-written Sobel. The GL capture path needs a real WebGL context and
 * is exercised by the Phase 9 e2e instead — asserting the maths here is what
 * keeps that e2e a smoke test rather than a unit test in disguise.
 */

import { describe, expect, it } from 'vitest';

import { sobelEdges } from './capture';
import { CLIENT_PACK_SHOTS, MODE_COPY, PRESETS_BY_ID, RENDER_PRESETS, randomSeed } from './presets';

describe('preset catalogue (mirror of services/render/types.py)', () => {
  it('interiors are Explore-only (spec F6)', () => {
    for (const preset of RENDER_PRESETS) {
      if (preset.scene === 'interior') {
        expect(preset.modes).toEqual(['explore']);
      } else {
        expect(preset.modes).toContain('precise');
      }
    }
  });

  it('explains both modes in plain words, no jargon (§15)', () => {
    for (const mode of ['precise', 'explore'] as const) {
      expect(MODE_COPY[mode].title.length).toBeGreaterThan(0);
      expect(MODE_COPY[mode].body.toLowerCase()).not.toContain('controlnet');
      expect(MODE_COPY[mode].body.toLowerCase()).not.toContain('denoise');
    }
  });
});

describe('client pack (mirror of services/render/pack.py)', () => {
  it('is 6 exteriors + living + kitchen, in zip order', () => {
    const scenes = CLIENT_PACK_SHOTS.map((s) => PRESETS_BY_ID.get(s.preset)?.scene);
    expect(scenes.filter((s) => s === 'exterior')).toHaveLength(6);
    const interiors = CLIENT_PACK_SHOTS.filter(
      (s) => PRESETS_BY_ID.get(s.preset)?.scene === 'interior',
    ).map((s) => s.preset);
    expect(interiors).toEqual(['interior-living', 'interior-kitchen']);
  });

  it('never requests a mode its preset refuses', () => {
    for (const shot of CLIENT_PACK_SHOTS) {
      const preset = PRESETS_BY_ID.get(shot.preset);
      expect(preset, `unknown preset ${shot.preset}`).toBeDefined();
      expect(preset?.modes).toContain(shot.mode);
    }
  });

  it('has unique slugs (they become zip filenames)', () => {
    const slugs = CLIENT_PACK_SHOTS.map((s) => s.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });
});

describe('sobelEdges (the §9 edge map, written by hand)', () => {
  it('a flat depth field has no edges — pure white', () => {
    const depth = new Float32Array(8 * 8).fill(0.5);
    const edges = sobelEdges(depth, 8, 8);
    expect([...edges].every((v) => v === 255)).toBe(true);
  });

  it('a depth step draws a dark vertical line at the step', () => {
    const width = 8;
    const height = 8;
    const depth = new Float32Array(width * height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        depth[y * width + x] = x < 4 ? 0.2 : 0.8;
      }
    }
    const edges = sobelEdges(depth, width, height);
    const midRow = 4 * width;
    // Dark exactly around the step (columns 3–4), white far from it.
    expect(edges[midRow + 3]).toBeLessThan(128);
    expect(edges[midRow + 4]).toBeLessThan(128);
    expect(edges[midRow + 0]).toBe(255);
    expect(edges[midRow + 7]).toBe(255);
  });

  it('is deterministic — the mock render contract depends on it', () => {
    const depth = Float32Array.from({ length: 16 }, (_, i) => (i % 5) / 5);
    expect([...sobelEdges(depth, 4, 4)]).toEqual([...sobelEdges(depth, 4, 4)]);
  });
});

describe('randomSeed', () => {
  it('stays a non-negative integer the API accepts (seed ≥ 0)', () => {
    for (let i = 0; i < 50; i += 1) {
      const seed = randomSeed();
      expect(Number.isInteger(seed)).toBe(true);
      expect(seed).toBeGreaterThanOrEqual(0);
      expect(seed).toBeLessThan(1_000_000);
    }
  });
});
