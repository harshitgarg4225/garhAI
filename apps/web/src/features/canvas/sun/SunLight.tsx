/**
 * SunLight.tsx — the sun, as scene lighting. One directional light (shadows)
 * plus one hemisphere fill, living in the SAME scene as the plan (§12: one
 * canvas — this component renders inside `<CanvasRoot>` and registers with
 * nothing else).
 *
 * PICKING NOTE (inherited fact 1): this module adds LIGHTS, not meshes.
 * Lights are not pickable and must not enter the `PickRegistry`; there is
 * deliberately no `usePickable` here, and nothing this component creates can
 * sit under a click.
 *
 * SCRUB = LIGHT-ONLY, BY CONSTRUCTION. The scrubber writes to `useSunStore`;
 * this component subscribes *transiently* (no React re-render) and copies a
 * freshly computed {@link SunFrame} onto the two light objects, then asks the
 * demand-mode renderer for one frame via `core.invalidate()`. No geometry, no
 * material recompiles, no store the meshes read. The model document is read
 * through `getState()` for north/city/bbox and is never written.
 *
 * SOFT SHADOWS. `CameraRig` already flips `gl.shadowMap.enabled` per mode;
 * this component sets the map type to PCFSoft once, at mount — before the
 * first 3D frame, so no compiled program has to be invalidated later.
 */

import { useEffect, useMemo } from 'react';
import { useThree } from '@react-three/fiber';
import { DirectionalLight, HemisphereLight, PCFSoftShadowMap, Vector3 } from 'three';

import { mmToWorldXYZ, useCanvasCore } from '../core';
import { useModelStore } from '../../../stores/model';
import { buildingExtentOf, type BuildingExtent } from './buildingBbox';
import { cityForPack, DEFAULT_CITY } from './cities';
import { computeSunFrame } from './frame';
import { useSunStore } from './sunStore';

/** Shadow map resolution. 2048² is crisp on a plot and cheap on one light. */
const SHADOW_MAP_SIZE = 2048;

/** Scratch vectors — module scope so a scrub allocates nothing. */
const centreWorld = /* @__PURE__ */ new Vector3();
const dirWorld = /* @__PURE__ */ new Vector3();

/** Fallback framing before any geometry exists (a 20 m stage). */
const EMPTY_EXTENT: BuildingExtent = {
  box: { minX: -10_000, minY: -10_000, maxX: 10_000, maxY: 10_000 },
  heightMm: 6_000,
};

export function SunLight(): JSX.Element {
  const core = useCanvasCore();
  const gl = useThree((s) => s.gl);

  const lights = useMemo(() => {
    const sun = new DirectionalLight(0xffffff, 0);
    sun.castShadow = true;
    sun.shadow.mapSize.set(SHADOW_MAP_SIZE, SHADOW_MAP_SIZE);
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    const hemi = new HemisphereLight(0xbfd4e8, 0x8a8073, 0.4);
    return { sun, hemi };
  }, []);

  // Soft-shadow filtering, set once at mount (see the header note).
  useEffect(() => {
    gl.shadowMap.type = PCFSoftShadowMap;
  }, [gl]);

  useEffect(() => {
    const { sun, hemi } = lights;

    // Building extent is model-dependent and *cached by document identity*:
    // recomputed when the house changes, never when the scrubber moves.
    let extentFor: { house: unknown; extent: BuildingExtent } | null = null;
    const extentOf = (): BuildingExtent => {
      const house = useModelStore.getState().doc.house;
      if (extentFor === null || extentFor.house !== house) {
        extentFor = { house, extent: buildingExtentOf(house) ?? EMPTY_EXTENT };
      }
      return extentFor.extent;
    };

    const sync = (): void => {
      const { day, minutesOfDay } = useSunStore.getState();
      const plot = useModelStore.getState().doc.plot;
      const city = cityForPack(plot.regProfile.cityPack) ?? DEFAULT_CITY;
      const frame = computeSunFrame(day, minutesOfDay, city.latDeg, city.lonDeg, plot.northDeg);
      const { box, heightMm } = extentOf();

      const is3d = core.viewport.mode === '3d';
      sun.visible = is3d && frame.aboveHorizon;
      hemi.visible = is3d;
      sun.intensity = frame.sunIntensity;
      hemi.intensity = frame.hemiIntensity;
      const [r, g, b] = frame.sunColor;
      sun.color.setRGB(r, g, b);

      // Frame the shadow camera around the building (world units = metres).
      const cx = (box.minX + box.maxX) / 2;
      const cy = (box.minY + box.maxY) / 2;
      const radiusMm = 0.5 * Math.hypot(box.maxX - box.minX, box.maxY - box.minY, heightMm);
      const radiusWorld = Math.max(radiusMm / 1000, 2);
      mmToWorldXYZ(cx, cy, heightMm / 2, centreWorld);
      // Model direction → world axes: worldX=+x, worldY=+z(up), worldZ=−y.
      dirWorld.set(frame.dirModel.x, frame.dirModel.z, -frame.dirModel.y);

      sun.position.copy(centreWorld).addScaledVector(dirWorld, radiusWorld * 2.5);
      sun.target.position.copy(centreWorld);
      sun.target.updateMatrixWorld();
      const cam = sun.shadow.camera;
      const half = radiusWorld * 1.15;
      if (cam.left !== -half || cam.far !== radiusWorld * 5) {
        cam.left = -half;
        cam.right = half;
        cam.top = half;
        cam.bottom = -half;
        cam.near = 0.1;
        cam.far = radiusWorld * 5;
        cam.updateProjectionMatrix();
      }

      core.invalidate();
    };

    sync();
    const unsubSun = useSunStore.subscribe(sync);
    // Model store: only the plot (north, city) and the house (extent) matter;
    // save-state churn must not re-aim the sun.
    let lastPlot = useModelStore.getState().doc.plot;
    let lastHouse = useModelStore.getState().doc.house;
    const unsubModel = useModelStore.subscribe((s) => {
      if (s.doc.plot !== lastPlot || s.doc.house !== lastHouse) {
        lastPlot = s.doc.plot;
        lastHouse = s.doc.house;
        sync();
      }
    });
    // Viewport: only the 2D↔3D flip matters, not every pan/orbit commit.
    let lastMode = core.viewport.mode;
    const unsubViewport = core.viewport.subscribe(() => {
      if (core.viewport.mode !== lastMode) {
        lastMode = core.viewport.mode;
        sync();
      }
    });
    return () => {
      unsubSun();
      unsubModel();
      unsubViewport();
    };
  }, [core, lights]);

  // Dispose GPU-side shadow resources with the component.
  useEffect(
    () => () => {
      lights.sun.dispose();
      lights.hemi.dispose();
    },
    [lights],
  );

  return (
    <>
      <primitive object={lights.sun} />
      <primitive object={lights.sun.target} />
      <primitive object={lights.hemi} />
    </>
  );
}
