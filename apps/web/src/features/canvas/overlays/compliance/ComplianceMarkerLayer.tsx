/**
 * ComplianceMarkerLayer.tsx — the on-canvas half of a compliance chip.
 *
 * A chip in the strip tells you a bedroom is undersized. A marker on the plan
 * tells you WHICH bedroom without making you click anything. The two are the
 * same data (`ComplianceChipVM` → `ComplianceMarker`), so they cannot disagree,
 * and hovering either highlights both.
 *
 * §14: two instanced meshes for every marker in the plan (a disc and a ring),
 * screen-constant size, matrices written on camera commit with no allocation
 * and no React render. Picks go through the core registry like everything else.
 *
 * Markers never intercept a tool. They register under the `'dimension'` pick
 * kind — the highest-priority kind in the core's table — ONLY when the layer is
 * explicitly interactive; the default is a non-pickable annotation, because a
 * violation badge that swallows the click you meant for the wall underneath it
 * is a compliance overlay that blocks work, which golden rule 5 forbids.
 */

import { useEffect, useMemo, useRef } from 'react';
import { CircleGeometry, Matrix4, Quaternion, Vector3 } from 'three';
import type { InstancedMesh } from 'three';

import { useCanvasCore, WORLD_UNITS_PER_MM } from '../../core';
import { ANNOTATION_RENDER_ORDER, getOverlayMaterials } from '../render/overlayMaterials';
import { useViewportEffect } from '../render/screenScale';
import type { ComplianceMarker } from './mapping';

/** Marker radius in CSS pixels. Big enough to see, small enough to ignore. */
export const MARKER_RADIUS_PX = 7;

export interface ComplianceMarkerLayerProps {
  markers: readonly ComplianceMarker[];
  elevationMm?: number | undefined;
  /** Keys of markers to draw at full strength; the rest are dimmed. */
  activeKeys?: readonly string[] | undefined;
  visible?: boolean | undefined;
}

const scratchMatrix = /* @__PURE__ */ new Matrix4();
const scratchPosition = /* @__PURE__ */ new Vector3();
const scratchScale = /* @__PURE__ */ new Vector3(1, 1, 1);
const FLAT = /* @__PURE__ */ new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), -Math.PI / 2);

export function ComplianceMarkerLayer({
  markers,
  elevationMm = 0,
  activeKeys,
  visible = true,
}: ComplianceMarkerLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const materials = getOverlayMaterials();

  // Split by severity so each colour is one instanced mesh — two draw calls for
  // the whole layer regardless of how many violations a plan has.
  const fails = useMemo(() => markers.filter((m) => m.status === 'fail'), [markers]);
  const warns = useMemo(() => markers.filter((m) => m.status === 'warn'), [markers]);

  const geometry = useMemo(() => new CircleGeometry(1, 16), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  const failRef = useRef<InstancedMesh | null>(null);
  const warnRef = useRef<InstancedMesh | null>(null);

  const active = useMemo(() => new Set(activeKeys ?? []), [activeKeys]);

  useViewportEffect(() => {
    const radiusMm = MARKER_RADIUS_PX * core.viewport.mmPerPx;

    const write = (mesh: InstancedMesh | null, list: readonly ComplianceMarker[]): void => {
      if (mesh === null) return;
      list.forEach((marker, index) => {
        // A marker the user is hovering or has selected grows slightly. Scale
        // rather than colour: the colour already carries severity, and two
        // meanings on one channel is how a red that means "selected" gets read
        // as a red that means "fails".
        const k = radiusMm * (active.size > 0 && active.has(marker.key) ? 1.35 : 1);
        scratchPosition.set(
          marker.atMm.x * WORLD_UNITS_PER_MM,
          elevationMm * WORLD_UNITS_PER_MM,
          -marker.atMm.y * WORLD_UNITS_PER_MM,
        );
        scratchScale.set(k * WORLD_UNITS_PER_MM, k * WORLD_UNITS_PER_MM, 1);
        scratchMatrix.compose(scratchPosition, FLAT, scratchScale);
        mesh.setMatrixAt(index, scratchMatrix);
      });
      mesh.count = list.length;
      mesh.instanceMatrix.needsUpdate = true;
      mesh.computeBoundingSphere();
    };

    write(failRef.current, fails);
    write(warnRef.current, warns);
  }, [fails, warns, active, elevationMm]);

  if (markers.length === 0) return null;

  return (
    <group visible={visible} name="compliance-marker-overlay">
      <instancedMesh
        key={`fail-${String(fails.length)}`}
        ref={failRef}
        args={[geometry, materials.markerFail, Math.max(1, fails.length)]}
        renderOrder={ANNOTATION_RENDER_ORDER}
        frustumCulled={false}
        // Not registered with the pick registry: a violation badge must not
        // swallow a click meant for the wall it sits on (golden rule 5).
        raycast={() => null}
      />
      <instancedMesh
        key={`warn-${String(warns.length)}`}
        ref={warnRef}
        args={[geometry, materials.markerWarn, Math.max(1, warns.length)]}
        renderOrder={ANNOTATION_RENDER_ORDER}
        frustumCulled={false}
        raycast={() => null}
      />
    </group>
  );
}
