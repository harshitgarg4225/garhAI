/**
 * ThreeDLayers — the 3D layer set, mounted INSIDE the same `<CanvasRoot>` the
 * 2D plan uses when the editor is in 3D mode (§12: one scene graph, one
 * picker; the `CameraRig` swaps the projection and this component supplies
 * what the perspective camera looks at).
 *
 * Composition only, like the plan renderer next door:
 *
 *   features/canvas/three    the extruded building (walls w/ opening cuts,
 *                            slabs, stairs, parapet, mumty, OHT), §14
 *                            incremental per-storey rebuild
 *   features/canvas/facade   kit components as separate picked meshes (§8's
 *                            isolated sub-model — reads the model store itself)
 *   features/canvas/sun      the date/time-driven directional light — which is
 *                            why `ThreeDScene` mounts with `lights={false}`
 *   Selection3D (below)      the 2D↔3D selection bridge, drawn from the same
 *                            selection store both views write
 *
 * PICKING (inherited fact 1): this file adds ONE kind of mesh of its own — the
 * selection rings — and they are `OutlinePolyline`s from the canvas core,
 * which are overlay geometry with no element identity; everything pickable
 * here (building buckets, facade components) registers inside its feature
 * module, and that is asserted in those modules' own headers and tests.
 *
 * TELEMETRY IS STORE STATE: `onEngineStatus` / `onRebuildStats` land in
 * `stores/three.ts`, so the status chip (DOM overlay), the shell and the
 * Playwright probe all read the same numbers instead of three private copies.
 */

import { useMemo } from 'react';

import type { Bbox, HouseModel } from '@garh/model';

import { OutlineBox } from '../../../features/canvas/core';
import { FacadeLayer } from '../../../features/canvas/facade';
import { SunLight } from '../../../features/canvas/sun';
import {
  ROOF_GROUP_KEY,
  storeyGroupKey,
  ThreeDScene,
  type RebuildStats,
} from '../../../features/canvas/three';
import { useSelectionStore } from '../../../stores/selection';
import { useThreeStore } from '../../../stores/three';
import { elementsExtentMm, storeyFflMm } from '../plan';

export interface ThreeDLayersProps {
  readonly house: HouseModel;
  /** `materialId -> colorHex` (each catalogue item's `swatchHex`). */
  readonly materialColors: Readonly<Record<string, string>> | undefined;
}

/**
 * Which rebuild groups the storey filter shows. `undefined` = everything.
 * The roof group (terrace slab, parapet, mumty, OHT) belongs to the TOP
 * storey's silhouette, so it stays visible only when that storey is the one
 * being inspected — a parapet floating over the ground floor would be the
 * view asserting geometry the filter hid.
 */
export function visibleGroupKeysFor(
  house: HouseModel,
  visibleStoreyId: string | null,
): ReadonlySet<string> | undefined {
  if (visibleStoreyId === null) return undefined;
  const keys = new Set<string>([storeyGroupKey(visibleStoreyId)]);
  const top = house.storeys[house.storeys.length - 1];
  if (top !== undefined && top.id === visibleStoreyId) keys.add(ROOF_GROUP_KEY);
  return keys;
}

export function ThreeDLayers({ house, materialColors }: ThreeDLayersProps): JSX.Element {
  const visibleStoreyId = useThreeStore((s) => s.visibleStoreyId);
  const noteEngineStatus = useThreeStore((s) => s.noteEngineStatus);
  const noteRebuild = useThreeStore((s) => s.noteRebuild);

  const visibleGroupKeys = useMemo(
    () => visibleGroupKeysFor(house, visibleStoreyId),
    [house, visibleStoreyId],
  );

  return (
    <>
      <SunLight />
      <ThreeDScene
        house={house}
        materialColors={materialColors}
        // The sun widget owns the lighting; two suns would double every shadow.
        lights={false}
        visibleGroupKeys={visibleGroupKeys}
        onEngineStatus={(status) =>
          noteEngineStatus(status.state, status.state === 'unavailable' ? status.reason : null)
        }
        onRebuildStats={(stats: RebuildStats) => noteRebuild(stats)}
      />
      {/* The facade is the building's exterior skin. With one storey isolated
          there is no exterior to hang it on, so it hides with the rest rather
          than floating around a slice of the building. The kit stays applied —
          this is visibility, never an op. */}
      {visibleStoreyId === null ? <FacadeLayer /> : null}
      <Selection3D house={house} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Selection bridge: what you picked in either view, ringed in this one
// ---------------------------------------------------------------------------

/**
 * A dashed ring on the selected element's plan footprint, at its storey's FFL.
 *
 * Honest scope: the 3D view highlights WHERE the selection is, not its full
 * silhouette — re-deriving a per-kind 3D outline would duplicate the mesh
 * synthesis the three module owns. Facade components are skipped because
 * `FacadeLayer` already boosts its own selected meshes, and ids the extent
 * helper cannot place (a deleted element still in the selection) draw nothing
 * rather than a ring at the origin.
 */
function Selection3D({ house }: { readonly house: HouseModel }): JSX.Element | null {
  const selectedIds = useSelectionStore((s) => s.ids);

  const rings = useMemo(() => {
    const out: { id: string; box: Bbox; elevationMm: number }[] = [];
    for (const id of selectedIds) {
      const box = elementsExtentMm(house, [id]);
      if (box === null) continue; // facade comps and unknown ids land here
      out.push({ id, box, elevationMm: elementStoreyFflMm(house, id) });
    }
    return out;
  }, [house, selectedIds]);

  if (rings.length === 0) return null;
  return (
    <>
      {rings.map((ring) => (
        <OutlineBox
          key={ring.id}
          boxMm={ring.box}
          // Nudged off the slab so the ring is not z-fighting the floor it
          // sits on. 3D overlays depth-test (constants.depthTestForMode), so
          // this is a real elevation, not a render-order trick.
          elevationMm={ring.elevationMm + 40}
          tone="selection"
          dashed
          layer="selection"
        />
      ))}
    </>
  );
}

/** FFL of the storey an element belongs to; ground datum when storey-less. */
export function elementStoreyFflMm(house: HouseModel, id: string): number {
  const storeyId =
    house.walls.find((w) => w.id === id)?.storeyId ??
    house.rooms.find((r) => r.id === id)?.storeyId ??
    house.stairs.find((s) => s.id === id)?.storeyId ??
    house.balconies.find((b) => b.id === id)?.storeyId ??
    house.columns.find((c) => c.id === id)?.storeyId ??
    house.furniture.find((f) => f.id === id)?.storeyId ??
    house.openings
      .map((o) => (o.id === id ? house.walls.find((w) => w.id === o.wallId)?.storeyId : undefined))
      .find((s) => s !== undefined) ??
    null;
  return storeyFflMm(house, storeyId);
}
