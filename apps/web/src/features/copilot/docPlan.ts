/**
 * docPlan.ts — a plan thumbnail's geometry, derived from a ProjectDoc.
 *
 * The copilot's before/after mini-canvases draw straight from two documents:
 * the store's current `doc` and the dry-run folded fork. This module turns
 * one storey of a document into flat draw lists (walls, opening cuts, room
 * labels) plus the mm-space viewBox math. Pure functions over `@garh/model`
 * types — no React, no stores — so the dry-fold → pixels path is testable.
 *
 * Why not reuse `features/options/planGeometry.ts`: that module reads a
 * SOLVER OPTION's op expansion (`wall.add` payloads), not a folded document.
 * The extraction differs even though the SVG at the end looks similar; the
 * viewBox convention (mm units, one Y-flip, `strokeFor`) is kept identical so
 * the two thumbnails render consistently side by side in the product.
 */

import { bbox, pointAlongSeg, polygonCentroid, roomDisplayName } from '@garh/model';
import type { ProjectDoc, Pt } from '@garh/model';

// ---------------------------------------------------------------------------
// Draw lists
// ---------------------------------------------------------------------------

export interface DocWallSeg {
  readonly id: string;
  readonly a: Pt;
  readonly b: Pt;
  readonly thicknessMm: number;
  readonly kind: string;
}

/** An opening drawn as a cut across its host wall. */
export interface DocOpeningMark {
  readonly id: string;
  /** Cut segment endpoints along the wall centreline, mm. */
  readonly a: Pt;
  readonly b: Pt;
  /** Host wall thickness — the cut must be at least as wide to read as a gap. */
  readonly wallThicknessMm: number;
  readonly kind: string;
}

export interface DocRoomLabel {
  readonly id: string;
  readonly label: string;
  readonly x: number;
  readonly y: number;
}

export interface DocPlanGeometry {
  readonly storeyId: string | null;
  readonly walls: readonly DocWallSeg[];
  readonly openings: readonly DocOpeningMark[];
  readonly labels: readonly DocRoomLabel[];
}

/** Everything drawable on one storey. Unknown storey → empty lists. */
export function docPlanForStorey(doc: ProjectDoc, storeyId: string | null): DocPlanGeometry {
  if (storeyId === null) return { storeyId, walls: [], openings: [], labels: [] };

  const walls = doc.house.walls
    .filter((w) => w.storeyId === storeyId)
    .map((w) => ({ id: w.id, a: w.a, b: w.b, thicknessMm: w.thicknessMm, kind: w.kind }));

  const wallById = new Map(doc.house.walls.map((w) => [w.id as string, w] as const));
  const openings: DocOpeningMark[] = [];
  for (const opening of doc.house.openings) {
    const wall = wallById.get(opening.wallId);
    if (wall === undefined || wall.storeyId !== storeyId) continue;
    const seg = { a: wall.a, b: wall.b };
    const half = Math.trunc(opening.widthMm / 2);
    openings.push({
      id: opening.id,
      a: pointAlongSeg(seg, opening.offsetMm - half),
      b: pointAlongSeg(seg, opening.offsetMm + half),
      wallThicknessMm: wall.thicknessMm,
      kind: opening.kind,
    });
  }

  const labels = doc.house.rooms
    .filter((r) => r.storeyId === storeyId)
    .map((r) => {
      const at = polygonCentroid(r.polygon);
      return { id: r.id, label: roomDisplayName(r), x: at.x, y: at.y };
    });

  return { storeyId, walls, openings, labels };
}

// ---------------------------------------------------------------------------
// Which storey should the diff draw?
// ---------------------------------------------------------------------------

/**
 * The storey a change touches, resolved from the touched element ids against
 * the AFTER document (added elements exist only there). Falls back to the
 * active storey, then the first storey — an honest guess beats a blank tile.
 */
export function pickDiffStoreyId(
  doc: ProjectDoc,
  elementIds: readonly string[],
  activeStoreyId: string | null,
): string | null {
  const wanted = new Set(elementIds);
  if (wanted.size > 0) {
    const h = doc.house;
    for (const s of h.storeys) if (wanted.has(s.id)) return s.id;
    for (const w of h.walls) if (wanted.has(w.id)) return w.storeyId;
    for (const r of h.rooms) if (wanted.has(r.id)) return r.storeyId;
    for (const o of h.openings) {
      if (!wanted.has(o.id)) continue;
      const wall = h.walls.find((w) => w.id === o.wallId);
      if (wall !== undefined) return wall.storeyId;
    }
    for (const s of h.stairs) if (wanted.has(s.id)) return s.storeyId;
    for (const b of h.balconies) if (wanted.has(b.id)) return b.storeyId;
    for (const c of h.columns) if (wanted.has(c.id)) return c.storeyId;
    for (const f of h.furniture) if (wanted.has(f.id)) return f.storeyId;
  }
  if (activeStoreyId !== null && doc.house.storeys.some((s) => s.id === activeStoreyId)) {
    return activeStoreyId;
  }
  return doc.house.storeys[0]?.id ?? null;
}

// ---------------------------------------------------------------------------
// ViewBox — mm space, single Y flip, no pixel math
// ---------------------------------------------------------------------------

export interface DocPlanView {
  readonly viewBox: string;
  /** Model mm → view mm (the one Y flip lives here). */
  readonly toView: (p: Pt) => { x: number; y: number };
  /** Stroke width for a wall of `thicknessMm`, view units (mm). */
  readonly strokeFor: (thicknessMm: number) => number;
  readonly labelFontMm: number;
}

/**
 * ViewBox over BOTH documents' geometry so the before and after tiles share
 * one frame — a wall that moves must not also make the whole plan jump.
 */
export function docPlanViewBox(geometries: readonly DocPlanGeometry[]): DocPlanView | null {
  const points: Pt[] = [];
  for (const g of geometries) {
    for (const w of g.walls) points.push(w.a, w.b);
    for (const l of g.labels) points.push({ x: l.x, y: l.y });
  }
  if (points.length === 0) return null;

  const b = bbox(points);
  const width = Math.max(b.maxX - b.minX, 1);
  const height = Math.max(b.maxY - b.minY, 1);
  const pad = Math.max(500, Math.round(Math.max(width, height) * 0.06));

  // Model +y is up; SVG +y is down. Map (x, y) → (x, -y) and set the viewBox
  // over the flipped range: y' ∈ [-(maxY+pad), -(minY-pad)].
  const minX = b.minX - pad;
  const minY = -(b.maxY + pad);
  const w = width + pad * 2;
  const h = height + pad * 2;

  const span = Math.max(w, h);
  return {
    viewBox: `${minX} ${minY} ${w} ${h}`,
    toView: (p) => ({ x: p.x, y: -p.y }),
    // Clamp so hairline internal walls stay visible at plot scale and a thick
    // external wall does not blob at room scale.
    strokeFor: (thicknessMm) => Math.min(Math.max(thicknessMm, span / 220), span / 40),
    labelFontMm: span / 22,
  };
}
