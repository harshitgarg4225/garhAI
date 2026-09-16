/**
 * geometry3d.test.ts — the triangle-soup contract, plus the one cross-module
 * pin that matters: this module's mm→world constant MUST equal the canvas
 * core's, because `geometry3d.ts` deliberately re-states it to stay
 * three-free. If either side changes scale, this is the failing test.
 */

import { describe, expect, it } from 'vitest';

import { WORLD_UNITS_PER_MM } from '../core';
import type { OrientedBoxMm } from './componentBoxes';
import { buildBoxTriangles, hexToRgb, WORLD_PER_MM } from './geometry3d';

const BOX: OrientedBoxMm = {
  cx: 3000,
  cy: 0,
  dirX: 1,
  dirY: 0,
  lenMm: 1500,
  depthMm: 600,
  baseElevMm: 2700,
  heightMm: 100,
  colorHex: '#2E2E2E',
};

describe('the world scale pin', () => {
  it('agrees with core/constants', () => {
    expect(WORLD_PER_MM).toBe(WORLD_UNITS_PER_MM);
  });
});

describe('hexToRgb', () => {
  it('parses 6-digit hex and falls back to grey on garbage', () => {
    expect(hexToRgb('#FFFFFF')).toEqual([1, 1, 1]);
    expect(hexToRgb('#000000')).toEqual([0, 0, 0]);
    const [r, g, b] = hexToRgb('not-a-colour');
    expect(r).toBeGreaterThan(0);
    expect(g).toBeGreaterThan(0);
    expect(b).toBeGreaterThan(0);
  });
});

describe('buildBoxTriangles', () => {
  it('emits 36 vertices per box with matching colour attributes', () => {
    const data = buildBoxTriangles([BOX, BOX]);
    expect(data.positions).toHaveLength(2 * 36 * 3);
    expect(data.colors).toHaveLength(data.positions.length);
  });

  it('positions live in the coords.ts frame: x=+mmX·s, y=elev·s, z=−mmY·s', () => {
    const data = buildBoxTriangles([BOX]);
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < data.positions.length; i += 3) {
      const x = data.positions[i] ?? 0;
      const y = data.positions[i + 1] ?? 0;
      const z = data.positions[i + 2] ?? 0;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
    // x spans cx ± len/2 (dir = +x), in metres.
    expect(minX).toBeCloseTo((3000 - 750) * WORLD_PER_MM, 6);
    expect(maxX).toBeCloseTo((3000 + 750) * WORLD_PER_MM, 6);
    // y spans the elevation range.
    expect(minY).toBeCloseTo(2700 * WORLD_PER_MM, 6);
    expect(maxY).toBeCloseTo(2800 * WORLD_PER_MM, 6);
    // depth runs along plan-y, which is NEGATIVE world z.
    expect(minZ).toBeCloseTo(-300 * WORLD_PER_MM, 6);
    expect(maxZ).toBeCloseTo(300 * WORLD_PER_MM, 6);
  });

  it('carries the RAW kit colour — no baked shading; the scene lights do that', () => {
    const data = buildBoxTriangles([BOX]);
    const [r, g, b] = hexToRgb(BOX.colorHex);
    for (let i = 0; i < data.colors.length; i += 3) {
      expect(data.colors[i]).toBeCloseTo(r, 6);
      expect(data.colors[i + 1]).toBeCloseTo(g, 6);
      expect(data.colors[i + 2]).toBeCloseTo(b, 6);
    }
  });

  it('emits one unit normal per vertex, six distinct outward directions per box', () => {
    const data = buildBoxTriangles([BOX]);
    expect(data.normals).toHaveLength(data.positions.length);
    const directions = new Set<string>();
    for (let i = 0; i < data.normals.length; i += 3) {
      const nx = data.normals[i] ?? 0;
      const ny = data.normals[i + 1] ?? 0;
      const nz = data.normals[i + 2] ?? 0;
      expect(Math.hypot(nx, ny, nz)).toBeCloseTo(1, 6);
      directions.add(`${nx.toFixed(3)},${ny.toFixed(3)},${nz.toFixed(3)}`);
    }
    expect(directions.size).toBe(6);
  });

  it('winds every triangle CCW seen from outside, agreeing with its normal (FrontSide contract)', () => {
    // A lit FrontSide material culls the back face: a box wound the wrong way
    // renders as a hole and casts no shadow. So (b−a)×(c−a) must point the
    // same way as the stated normal, and outward from the box centre.
    const data = buildBoxTriangles([BOX]);
    const cx = BOX.cx * WORLD_PER_MM;
    const cy = (BOX.baseElevMm + BOX.heightMm / 2) * WORLD_PER_MM;
    const cz = -BOX.cy * WORLD_PER_MM;
    for (let i = 0; i + 8 < data.positions.length; i += 9) {
      const p = data.positions;
      const ax = p[i] ?? 0;
      const ay = p[i + 1] ?? 0;
      const az = p[i + 2] ?? 0;
      const ux = (p[i + 3] ?? 0) - ax;
      const uy = (p[i + 4] ?? 0) - ay;
      const uz = (p[i + 5] ?? 0) - az;
      const vx = (p[i + 6] ?? 0) - ax;
      const vy = (p[i + 7] ?? 0) - ay;
      const vz = (p[i + 8] ?? 0) - az;
      const crx = uy * vz - uz * vy;
      const cry = uz * vx - ux * vz;
      const crz = ux * vy - uy * vx;
      const nx = data.normals[i] ?? 0;
      const ny = data.normals[i + 1] ?? 0;
      const nz = data.normals[i + 2] ?? 0;
      expect(crx * nx + cry * ny + crz * nz).toBeGreaterThan(0);
      // Outward: the normal points away from the box centre.
      expect((ax - cx) * nx + (ay - cy) * ny + (az - cz) * nz).toBeGreaterThan(0);
    }
  });

  it('the selection boost brightens every vertex', () => {
    const plain = buildBoxTriangles([BOX], 1);
    const boosted = buildBoxTriangles([BOX], 1.18);
    for (let i = 0; i < plain.colors.length; i += 1) {
      const p = plain.colors[i] ?? 0;
      const b = boosted.colors[i] ?? 0;
      expect(b).toBeGreaterThanOrEqual(p);
    }
  });

  it('is deterministic', () => {
    const a = buildBoxTriangles([BOX]);
    const b = buildBoxTriangles([BOX]);
    expect(Array.from(a.positions)).toEqual(Array.from(b.positions));
    expect(Array.from(a.colors)).toEqual(Array.from(b.colors));
  });
});
