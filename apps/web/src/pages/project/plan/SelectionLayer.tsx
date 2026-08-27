/**
 * SelectionLayer — the outline around what is picked, hovered, or in breach.
 *
 * Three treatments, one component, because they are the same drawing problem
 * and an architect reads them together: the wall you are dragging, the wall
 * under the cursor, and the room a compliance chip is pointing at.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY REACT IS THE RIGHT TOOL HERE, AND NOT IN `PreviewLayer`
 * ════════════════════════════════════════════════════════════════════════════
 * A selection changes when someone clicks — a few times a minute. A preview
 * changes on every pointer move — sixty times a second. The first is worth a
 * reconcile for the clarity; the second is not, which is why the preview owns a
 * raw buffer and this owns components.
 *
 * The outlines come from the core's `OutlinePolyline` / `OutlineFill`, which
 * draw with drei's screen-space-width `<Line>` because WebGL ignores
 * `LineBasicMaterial.linewidth`. Reusing them is what makes a selection in the
 * plan look identical to a selection in the Phase-5 3D view.
 *
 * Nothing here registers a pick target: an outline is feedback, not a thing you
 * can click. The elements underneath stay the pick targets, and `PlanScene`
 * already registered them.
 */

import { useMemo } from 'react';

import type { HouseModel, Pt } from '@garh/model';

import { OutlineFill, OutlinePolyline, type OutlineTone } from '../../../features/canvas/core';
import { columnRingMm, openingSymbol, stairSymbol, wallRingMm } from './planGeometry';

/** How many outlines are worth drawing before the drawing becomes noise. */
const MAX_OUTLINES = 60;

/**
 * The ring that represents an element in plan, or null when it has none.
 *
 * Furniture is deliberately absent: `features/canvas/furniture` draws its own
 * selection ring from the catalogue footprint, and a second outline from a
 * second source is how two subtly different rectangles end up on screen.
 */
export function ringForId(house: HouseModel, id: string): readonly Pt[] | null {
  for (const wall of house.walls) {
    if (wall.id === id) {
      const ring = wallRingMm(wall);
      return ring.length === 0 ? null : ring;
    }
  }
  for (const room of house.rooms) {
    if (room.id === id) return room.polygon.length >= 3 ? room.polygon : null;
  }
  for (const opening of house.openings) {
    if (opening.id !== id) continue;
    const wall = house.walls.find((w) => w.id === opening.wallId);
    if (wall === undefined) return null;
    const symbol = openingSymbol(wall, opening);
    return symbol === null ? null : symbol.ringMm;
  }
  for (const stair of house.stairs) {
    if (stair.id === id) return stairSymbol(stair).ringMm;
  }
  for (const balcony of house.balconies) {
    if (balcony.id === id) return balcony.polygon.length >= 3 ? balcony.polygon : null;
  }
  for (const column of house.columns) {
    if (column.id === id) return columnRingMm(column);
  }
  return null;
}

interface RingSet {
  readonly id: string;
  readonly ring: readonly Pt[];
}

function ringsFor(house: HouseModel, ids: readonly string[]): RingSet[] {
  const out: RingSet[] = [];
  for (const id of ids) {
    if (out.length >= MAX_OUTLINES) break;
    const ring = ringForId(house, id);
    if (ring !== null) out.push({ id, ring });
  }
  return out;
}

export interface SelectionLayerProps {
  readonly house: HouseModel;
  readonly elevationMm: number;
  readonly selectedIds: readonly string[];
  readonly hoverId: string | null;
  /** Elements a visible compliance chip points at. Drawn in the fail colour. */
  readonly violationIds?: readonly string[] | undefined;
}

export function SelectionLayer({
  house,
  elevationMm,
  selectedIds,
  hoverId,
  violationIds,
}: SelectionLayerProps): JSX.Element {
  const selected = useMemo(() => ringsFor(house, selectedIds), [house, selectedIds]);

  // The hover outline is suppressed for something already selected: two rings
  // on one wall reads as a bug, not as emphasis.
  const hovered = useMemo(() => {
    if (hoverId === null || selectedIds.includes(hoverId)) return null;
    const ring = ringForId(house, hoverId);
    return ring === null ? null : { id: hoverId, ring };
  }, [house, hoverId, selectedIds]);

  const violations = useMemo(() => {
    if (violationIds === undefined || violationIds.length === 0) return [];
    return ringsFor(house, violationIds);
  }, [house, violationIds]);

  return (
    <group name="selection">
      {violations.map(({ id, ring }) => (
        <Ring key={`violation-${id}`} ring={ring} elevationMm={elevationMm} tone="violation" fill />
      ))}
      {hovered === null ? null : (
        <Ring ring={hovered.ring} elevationMm={elevationMm} tone="hover" />
      )}
      {selected.map(({ id, ring }) => (
        <Ring key={`selected-${id}`} ring={ring} elevationMm={elevationMm} tone="selection" fill />
      ))}
    </group>
  );
}

function Ring({
  ring,
  elevationMm,
  tone,
  fill = false,
}: {
  readonly ring: readonly Pt[];
  readonly elevationMm: number;
  readonly tone: OutlineTone;
  readonly fill?: boolean | undefined;
}): JSX.Element {
  return (
    <>
      {fill && ring.length >= 3 ? (
        <OutlineFill polygonMm={ring} elevationMm={elevationMm} tone={tone} layer="selection" />
      ) : null}
      <OutlinePolyline
        pointsMm={ring}
        elevationMm={elevationMm}
        tone={tone}
        closed
        layer="selection"
      />
    </>
  );
}
