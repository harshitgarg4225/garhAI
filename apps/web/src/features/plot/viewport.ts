/**
 * Viewport math for the SVG plot editor — pure, so the component tests can
 * aim pointer events in model space and a Fast-Refresh boundary stays clean
 * (a component file that exports functions loses hot reload).
 *
 * The viewport is frozen to the COMMITTED boundary, so a drag cannot move the
 * camera it is being measured against. Model +Y is north/up; SVG y grows
 * downward, so `toSvg` flips. Label anchors and normals may be floats because
 * they never leave the screen.
 */

import type { Polygon, Pt } from '@garh/model';

import { ringAt } from './geometry';

export interface Viewport {
  readonly minX: number;
  readonly maxY: number;
  readonly pad: number;
  readonly vbW: number;
  readonly vbH: number;
  readonly span: number;
}

export function makeViewport(boundary: Polygon): Viewport {
  let minX = 0;
  let minY = 0;
  let maxX = 9144;
  let maxY = 12192;
  if (boundary.length > 0) {
    minX = Math.min(...boundary.map((p) => p.x));
    minY = Math.min(...boundary.map((p) => p.y));
    maxX = Math.max(...boundary.map((p) => p.x));
    maxY = Math.max(...boundary.map((p) => p.y));
  }
  const span = Math.max(maxX - minX, maxY - minY, 1000);
  const pad = Math.max(2500, Math.round(span / 5));
  return { minX, maxY, pad, vbW: maxX - minX + 2 * pad, vbH: maxY - minY + 2 * pad, span };
}

/** Model mm -> SVG user units (y flipped: model +Y is north/up). */
export function toSvg(vp: Viewport, p: Pt): { x: number; y: number } {
  return { x: p.x - vp.minX + vp.pad, y: vp.maxY - p.y + vp.pad };
}

/** Pointer event -> model mm, honouring preserveAspectRatio="xMidYMid meet". */
export function clientToModel(
  vp: Viewport,
  svg: SVGSVGElement,
  clientX: number,
  clientY: number,
): Pt {
  const rect = svg.getBoundingClientRect();
  const scale = Math.min(rect.width / vp.vbW, rect.height / vp.vbH);
  const ox = (rect.width - vp.vbW * scale) / 2;
  const oy = (rect.height - vp.vbH * scale) / 2;
  const xvb = (clientX - rect.left - ox) / scale;
  const yvb = (clientY - rect.top - oy) / scale;
  return {
    x: Math.round(xvb - vp.pad + vp.minX),
    y: Math.round(vp.maxY - (yvb - vp.pad)),
  };
}

/** Grid step that yields a readable line count for the current span. */
export function gridStep(span: number): number {
  for (const step of [500, 1000, 2000, 5000, 10000]) {
    if (span / step <= 40) return step;
  }
  return 20000;
}

/** Float outward normal (unit) of edge i for a CCW ring. Rendering only. */
export function outwardNormal(boundary: Polygon, i: number): { x: number; y: number } {
  const a = ringAt(boundary, i);
  const b = ringAt(boundary, i + 1);
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) return { x: 0, y: 0 };
  // Right side of a->b: outward for CCW (the model's storage convention).
  return { x: dy / len, y: -dx / len };
}
