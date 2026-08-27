/**
 * UnderlayLayer.tsx — the scanned plan, textured onto one quad under everything.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * IT IS NOT A PICK TARGET, AND THAT IS THE POINT
 * ════════════════════════════════════════════════════════════════════════════
 * This layer never touches `PickRegistry`. Not `usePickable`, not
 * `usePickableResolver`, nothing. An underlay covers the entire drawing area by
 * construction, so registering it would put a pick candidate under every single
 * pixel of the plan — and while `PICK_PRIORITY` would let walls win, an empty
 * click that currently means "deselect" would start meaning "you hit the
 * underlay". A tracing aid that steals clicks from the walls being traced over
 * it is worse than no tracing aid.
 *
 * That decision is what makes drag-to-move impossible to do the ordinary way:
 * there is nothing to grab. `UnderlayPanel` therefore takes the pointer
 * explicitly in an armed "Move" mode, and offers nudge buttons for fine work.
 *
 * There are also no react-three-fiber pointer handlers here, per the §12 rule
 * in `pickRegistry.ts` — which follows for free once nothing is pickable.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHERE IT SITS
 * ════════════════════════════════════════════════════════════════════════════
 * `originXMm/originYMm` is the model position of image pixel (0,0) — the scan's
 * TOP-LEFT corner — and the image runs east and south from there, because image
 * rows grow downward while model Y grows north. The quad is therefore centred
 * half a width east and half a height south of the origin.
 *
 * `PlaneGeometry` is authored in local XY with +Y up and its texture's top row
 * at local +Y (`flipY` is on by default for anything `TextureLoader` produces).
 * Rotated −90° about X, local +X → world +X and local +Y → world −Z, which is
 * model north. So the scan's top-left lands at the model's north-west corner of
 * the quad, exactly where the origin says it should, with no UV fiddling.
 *
 * Depth: drawn one millimetre BELOW the storey's finished floor level and with
 * a render order below the grid's, so the drafting grid, the plot boundary and
 * every wall read over the top of it rather than fighting it for the same
 * fragment. It writes no depth of its own, so nothing later is occluded by it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * TEXTURES, LEAKS AND EXPIRING URLS
 * ════════════════════════════════════════════════════════════════════════════
 * The image URL is a §13 presigned GET with a ≤10 minute TTL, and a plan tab
 * stays open for hours. A load failure is therefore FIRST assumed to be an
 * expired signature: the record is re-fetched once (which mints a fresh URL)
 * and the load retried. Only if that fails too does the quiet inline message
 * appear — never a thrown error, never an empty canvas with no explanation.
 *
 * Every texture is disposed when it is replaced and when this layer unmounts,
 * the same discipline `PlanScene.useGeometry` applies to buffers: a scan is
 * tens of megabytes of VRAM, and re-uploading one per URL refresh over an
 * afternoon is the "tab gets slower the longer you draw" bug with a bigger
 * constant.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  DoubleSide,
  MeshBasicMaterial,
  PlaneGeometry,
  SRGBColorSpace,
  TextureLoader,
  type Texture,
} from 'three';

import { ptRound, type Pt } from '@garh/model';

import {
  LAYER_RENDER_ORDER,
  OutlinePolyline,
  useCanvasCore,
  WORLD_UNITS_PER_MM,
} from '../canvas/core';
import { useUnderlayStore } from './store';

/**
 * How far below the storey FFL the quad sits, in mm.
 *
 * Enough that the orthographic depth buffer (400 m of range) separates it from
 * the plan cleanly, small enough that the 3D view — where the underlay is not
 * mounted at all — would never show it poking through a floor slab.
 */
const UNDERLAY_DROP_MM = 1;

/** Below `grid` (0) so the drafting grid draws over the scan, not under it. */
const UNDERLAY_RENDER_ORDER = LAYER_RENDER_ORDER.grid - 5;

/** Anisotropic filtering for the scan. Three clamps this to the GPU's max. */
const UNDERLAY_ANISOTROPY = 4;

const IMAGE_FAILED_MESSAGE =
  "The underlay image couldn't be loaded. Re-open the tab, or upload the scan again.";

export interface UnderlayLayerProps {
  /** Finished floor level of the storey being drawn, mm. */
  readonly elevationMm: number;
}

export function UnderlayLayer({ elevationMm }: UnderlayLayerProps): JSX.Element | null {
  const core = useCanvasCore();
  const record = useUnderlayStore((s) => s.record);
  const imageNonce = useUnderlayStore((s) => s.imageNonce);
  const marks = useUnderlayStore((s) => s.marks);
  const mode = useUnderlayStore((s) => s.mode);

  const [texture, setTexture] = useState<Texture | null>(null);

  // One geometry and one material for the life of the layer. Both are mutated
  // in place afterwards — a new scan is a new texture, not a new quad.
  const geometry = useMemo(() => new PlaneGeometry(1, 1), []);
  const material = useMemo(
    () =>
      new MeshBasicMaterial({
        transparent: true,
        // No depth writes: the underlay must never occlude anything drawn
        // after it, whatever the render order ends up being.
        depthWrite: false,
        // `toneMapped: false` keeps a scan looking like the paper it came
        // from; the plan view has no tone mapping to be consistent with.
        toneMapped: false,
        side: DoubleSide,
      }),
    [],
  );

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  const objectKey = record?.objectKey ?? null;
  const imageUrl = record?.imageUrl ?? null;

  // One retry per image. Reset when the underlying object changes, so a
  // replacement scan gets its own budget rather than inheriting an exhausted
  // one from the image it replaced.
  const retriedRef = useRef(false);
  useEffect(() => {
    retriedRef.current = false;
  }, [objectKey]);

  useEffect(() => {
    if (imageUrl === null) return undefined;

    let cancelled = false;
    let loaded: Texture | null = null;
    const loader = new TextureLoader();
    // The image comes from object storage on another origin; without this the
    // texture upload taints the context and three refuses it.
    loader.setCrossOrigin('anonymous');

    loader.load(
      imageUrl,
      (next) => {
        if (cancelled) {
          next.dispose();
          return;
        }
        next.colorSpace = SRGBColorSpace;
        next.anisotropy = UNDERLAY_ANISOTROPY;
        loaded = next;
        setTexture(next);
        useUnderlayStore.getState().setImageError(null);
        core.invalidate();
      },
      undefined,
      () => {
        if (cancelled) return;
        if (!retriedRef.current) {
          // Assume the signature expired (§13, ≤10 min) rather than that the
          // object is gone: re-fetch the record for a fresh URL, which bumps
          // `imageNonce` and re-runs this effect.
          retriedRef.current = true;
          void useUnderlayStore
            .getState()
            .refreshImageUrl()
            .then((ok) => {
              if (!cancelled && !ok) {
                useUnderlayStore.getState().setImageError(IMAGE_FAILED_MESSAGE);
              }
            });
          return;
        }
        useUnderlayStore.getState().setImageError(IMAGE_FAILED_MESSAGE);
      },
    );

    return () => {
      cancelled = true;
      // Cleared before disposal so the mesh never holds a freed texture, even
      // for the one commit between this cleanup and the next effect run.
      setTexture(null);
      if (loaded !== null) loaded.dispose();
    };
    // `imageNonce` is a dependency on purpose: a re-signed URL for the same
    // object can come back byte-identical, and without the nonce the retry
    // would not re-run.
  }, [core, imageUrl, imageNonce]);

  // Texture and opacity are material mutations, not React state on a mesh —
  // and `frameloop="demand"` means each one has to ask for its frame.
  const opacity = record?.opacity ?? 1;
  useEffect(() => {
    material.map = texture;
    material.opacity = opacity;
    material.needsUpdate = true;
    core.invalidate();
  }, [core, material, texture, opacity]);

  // Position and size, in world units. Recomputed on a calibration change,
  // which is a handful of times a session — not per frame.
  const placement = useMemo(() => {
    if (record === null) return null;
    const widthMm = record.widthPx * record.mmPerPx;
    const heightMm = record.heightPx * record.mmPerPx;
    return {
      widthWorld: widthMm * WORLD_UNITS_PER_MM,
      heightWorld: heightMm * WORLD_UNITS_PER_MM,
      // The image runs east and SOUTH of its origin, so the centre is half a
      // height below (−Y in model space) the origin's northing.
      centreXWorld: (record.originXMm + widthMm / 2) * WORLD_UNITS_PER_MM,
      centreZWorld: -(record.originYMm - heightMm / 2) * WORLD_UNITS_PER_MM,
    };
  }, [record]);

  useEffect(() => {
    core.invalidate();
  }, [core, placement, record?.visible]);

  // The two calibration marks, as a rubber band the user can see is straight.
  const markPoints = useMemo<readonly Pt[]>(
    () => marks.map((mark) => ptRound(mark.x, mark.y)),
    [marks],
  );

  if (record === null || placement === null) return null;

  const worldY = (elevationMm - UNDERLAY_DROP_MM) * WORLD_UNITS_PER_MM;

  return (
    <group name="underlay">
      <mesh
        geometry={geometry}
        material={material}
        position={[placement.centreXWorld, worldY, placement.centreZWorld]}
        scale={[placement.widthWorld, placement.heightWorld, 1]}
        rotation-x={-Math.PI / 2}
        renderOrder={UNDERLAY_RENDER_ORDER}
        visible={record.visible && texture !== null}
        // The quad is one primitive covering the whole scan; three's culling
        // test against a unit bounding sphere scaled by a non-uniform matrix
        // is exactly the case that blinks geometry at the frustum edge.
        frustumCulled={false}
      />

      {/* The calibration rubber band. Drawn in the scene rather than the DOM so
          it stays glued to the drawing while the wheel zooms underneath it. */}
      {mode === 'calibrate' && markPoints.length >= 2 ? (
        <OutlinePolyline
          pointsMm={markPoints}
          elevationMm={elevationMm}
          tone="preview"
          layer="preview"
        />
      ) : null}
    </group>
  );
}

export default UnderlayLayer;
