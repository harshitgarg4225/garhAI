/**
 * tags.ts — room tag view models. PURE.
 *
 * The rooms themselves come from `packages/model/src/rooms.ts` — planar
 * subdivision with Jaccard id preservation, folded into `house.rooms` by
 * `fold`. This module RENDERS that; it does not re-detect anything. If a tag
 * disagrees with the room it labels, the bug is upstream and this file must not
 * paper over it with a second opinion about where rooms are.
 *
 * Two numbers a tag needs that the model does not carry:
 *
 *  · **an anchor that is inside the room.** A polygon centroid falls outside an
 *    L-shaped room, and a label floating in the corridor next door is worse than
 *    no label. {@link roomAnchorMm} falls back to the centroid of the room's
 *    largest triangle, which is inside any simple polygon by construction.
 *
 *  · **a footprint.** Placement needs a box before troika has laid the glyphs
 *    out, so the width is estimated from the character count. The estimate is
 *    deliberately generous (see {@link AVG_GLYPH_RATIO}): over-estimating pushes
 *    labels slightly further apart than necessary, under-estimating lets two of
 *    them overlap, and only one of those is visible to the user.
 */

import {
  polygonAreaMm2,
  polygonCentroid,
  polygonContains,
  triangulate,
  roomDisplayName,
  ROOM_TYPE_LABELS,
  type Pt,
  type Room,
  type UnitsDisplay,
} from '@garh/model';

import { roomAreaText } from '../format';
import type { PlaceableLabel, PlacePointF } from './placement';

// ---------------------------------------------------------------------------
// Text metrics
// ---------------------------------------------------------------------------

/**
 * Mean glyph advance as a fraction of font size, for the UI stack at the
 * weights the tags use. Measured against Inter's metrics for mixed-case Latin
 * with digits; rounded UP from ~0.52 so the estimate errs wide.
 */
export const AVG_GLYPH_RATIO = 0.58;

/** Estimated rendered width of a string, in the same unit as `fontSize`. */
export function estimateTextWidth(text: string, fontSize: number): number {
  return text.length * fontSize * AVG_GLYPH_RATIO;
}

// ---------------------------------------------------------------------------
// Anchors
// ---------------------------------------------------------------------------

/**
 * A point guaranteed to be inside the room polygon.
 *
 * Centroid first — it is the visual centre for the convex rooms that make up
 * most of a house. When the room is L-shaped or U-shaped and the centroid falls
 * outside, the largest ear of the exact integer triangulation is used instead:
 * its centroid is strictly interior, and `triangulate` is the same routine the
 * areas and the sheet engine use, so the tag cannot land somewhere the drawing
 * disagrees with.
 */
export function roomAnchorMm(polygon: readonly Pt[]): PlacePointF {
  if (polygon.length < 3) return { x: 0, y: 0 };
  const centroid = polygonCentroid(polygon);
  if (polygonContains(polygon, centroid)) return centroid;

  const triangles = triangulate(polygon);
  let bestArea = -1;
  let best: PlacePointF = centroid;
  for (const t of triangles) {
    const area = Math.abs(
      (t[1].x - t[0].x) * (t[2].y - t[0].y) - (t[2].x - t[0].x) * (t[1].y - t[0].y),
    );
    if (area > bestArea) {
      bestArea = area;
      best = {
        x: (t[0].x + t[1].x + t[2].x) / 3,
        y: (t[0].y + t[1].y + t[2].y) / 3,
      };
    }
  }
  return best;
}

// ---------------------------------------------------------------------------
// View model
// ---------------------------------------------------------------------------

export interface RoomTagVM {
  readonly roomId: string;
  readonly storeyId: string;
  /** "Master Bedroom" — the room's name, or its type label as a fallback. */
  readonly nameText: string;
  /** "153.4 sq ft" / "14.2 m²", per project units. */
  readonly areaText: string;
  /** The target the brief or the solver set, formatted, or null. */
  readonly targetText: string | null;
  readonly areaMm2: number;
  readonly type: Room['type'];
  readonly locked: boolean;
  readonly anchorMm: PlacePointF;
  readonly polygonMm: readonly Pt[];
}

export interface RoomTagOptions {
  /** Rooms below this are too small to carry a tag at all. Default 0.5 m². */
  readonly minAreaMm2?: number | undefined;
  /** Skip these room types — voids and ducts are not rooms an architect names. */
  readonly hiddenTypes?: readonly Room['type'][] | undefined;
}

/**
 * Types that get no tag by default. A shaft or a duct is a hole in the slab,
 * not a room, and labelling it clutters the drawing with something nobody
 * needs to read.
 */
const DEFAULT_HIDDEN_TYPES: readonly Room['type'][] = ['void', 'shaft', 'duct'];

const DEFAULT_MIN_AREA_MM2 = 500_000; // 0.5 m², the model's own room floor

/**
 * Build the tag for every room on a storey, in a deterministic order (largest
 * first, ties on id — the same order placement wants, so the two agree).
 */
export function roomTags(
  rooms: readonly Room[],
  storeyId: string,
  display: UnitsDisplay,
  options: RoomTagOptions = {},
): RoomTagVM[] {
  const minAreaMm2 = options.minAreaMm2 ?? DEFAULT_MIN_AREA_MM2;
  const hidden = new Set(options.hiddenTypes ?? DEFAULT_HIDDEN_TYPES);

  const out: RoomTagVM[] = [];
  // "Room 1", "Room 2"… for unassigned rooms. Numbered in the model's own room
  // order so the numbering is stable across a re-render, and re-derived (not
  // stored) so a deleted room does not leave a gap in the sequence.
  let unassignedOrdinal = 0;

  for (const room of rooms) {
    if (room.storeyId !== storeyId) continue;
    if (hidden.has(room.type)) continue;
    if (room.areaMm2 < minAreaMm2) continue;
    if (room.polygon.length < 3) continue;

    if (room.type === 'unassigned' && room.name === '') unassignedOrdinal += 1;

    out.push({
      roomId: room.id,
      storeyId: room.storeyId,
      nameText: roomDisplayName(room, unassignedOrdinal),
      areaText: roomAreaText(room.areaMm2, display),
      targetText:
        room.targetAreaMm2 === null ? null : roomAreaText(room.targetAreaMm2, display),
      areaMm2: room.areaMm2,
      type: room.type,
      locked: room.locked,
      anchorMm: roomAnchorMm(room.polygon),
      polygonMm: room.polygon,
    });
  }

  out.sort((a, b) => b.areaMm2 - a.areaMm2 || (a.roomId < b.roomId ? -1 : 1));
  return out;
}

/** Human label for a room type — used by the inspector's type picker too. */
export function roomTypeLabel(type: Room['type']): string {
  return ROOM_TYPE_LABELS[type];
}

// ---------------------------------------------------------------------------
// Tags → placeable labels
// ---------------------------------------------------------------------------

export interface TagStyle {
  /** Name line, CSS pixels. */
  readonly nameFontPx: number;
  /** Area line, CSS pixels. */
  readonly areaFontPx: number;
  /** Gap between the two lines, CSS pixels. */
  readonly lineGapPx: number;
  /** Padding around the text block, CSS pixels. */
  readonly paddingPx: number;
}

export const DEFAULT_TAG_STYLE: TagStyle = {
  nameFontPx: 12,
  areaFontPx: 10.5,
  lineGapPx: 2,
  paddingPx: 3,
};

/**
 * Convert tags into the boxes the placer packs.
 *
 * `mmPerPx` is what turns a screen-constant label into a world footprint, and
 * it is the ONLY place zoom enters the tag pipeline. Everything upstream is
 * zoom-free and therefore cacheable across a pan.
 */
export function tagsToPlaceable(
  tags: readonly RoomTagVM[],
  mmPerPx: number,
  style: TagStyle = DEFAULT_TAG_STYLE,
): PlaceableLabel[] {
  return tags.map((tag) => {
    const widthPx =
      Math.max(
        estimateTextWidth(tag.nameText, style.nameFontPx),
        estimateTextWidth(tag.areaText, style.areaFontPx),
      ) +
      style.paddingPx * 2;
    const heightPx =
      style.nameFontPx + style.lineGapPx + style.areaFontPx + style.paddingPx * 2;

    return {
      id: tag.roomId,
      anchorMm: tag.anchorMm,
      halfWidthMm: (widthPx * mmPerPx) / 2,
      halfHeightMm: (heightPx * mmPerPx) / 2,
      priority: tag.areaMm2,
      boundaryMm: tag.polygonMm,
    };
  });
}

/**
 * Whether a room is big enough on SCREEN to carry a readable tag.
 *
 * Zoomed out to a site plan, a 1.2 m² WC is four pixels across and its tag is
 * eighty; showing it would bury the drawing under leader lines. The threshold
 * is the label's own height, which is self-scaling: nothing is hidden that
 * there is room for.
 */
export function tagFitsOnScreen(
  tag: RoomTagVM,
  mmPerPx: number,
  style: TagStyle = DEFAULT_TAG_STYLE,
): boolean {
  const minSideMm = (style.nameFontPx + style.areaFontPx) * mmPerPx;
  return polygonAreaMm2(tag.polygonMm) >= minSideMm * minSideMm;
}
