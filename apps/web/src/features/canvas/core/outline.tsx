/**
 * outline.tsx — selection, hover, preview and violation rendering primitives.
 *
 * Every module that can be selected draws its highlight with these, so
 * "selected" looks identical on a wall, a room, a piece of furniture and a
 * dimension without four modules agreeing on a colour by accident. They are
 * plain plan-space primitives — a polygon, a polyline, a box — because that is
 * what every element reduces to once you have its footprint.
 *
 * WHY drei's `<Line>` FOR THE OUTLINE. WebGL ignores
 * `LineBasicMaterial.linewidth`: every `THREE.Line` is exactly one device
 * pixel, which on a 2× display is half a CSS pixel and effectively invisible as
 * a selection cue. drei's `Line` renders a screen-space-width quad strip
 * (`Line2`/`LineMaterial` from three-stdlib, both MIT and already installed
 * with `@react-three/drei`), so a 2 px outline is 2 px at every zoom and on
 * every display. Hairline geometry that genuinely wants 1 px keeps using the
 * cheap materials in `materials.ts`.
 *
 * WHY `triangulate` FROM `@garh/model` FOR THE FILL. It is exact integer
 * ear-clipping and it is the same triangulation the areas and the sheet engine
 * use. Handing the polygon to three's `ShapeGeometry` instead would introduce a
 * second, floating-point notion of "the inside of this room", and the two would
 * disagree at exactly the concave corners that matter.
 */

import { useEffect, useMemo, useState } from 'react';
import { Line } from '@react-three/drei';

import type { Bbox, Polygon, Pt } from '@garh/model';

import { LAYER_RENDER_ORDER, type CanvasLayer } from './constants';
import { getCanvasMaterials, getCanvasThemeColors, watchCanvasTheme } from './materials';
import { bboxRingMm, pointsMmToWorld, polygonFillGeometry } from './outlineGeometry';

// ---------------------------------------------------------------------------
// Tones
// ---------------------------------------------------------------------------

export type OutlineTone = 'selection' | 'hover' | 'preview' | 'violation';

const TONE_LINE_WIDTH: Readonly<Record<OutlineTone, number>> = {
  selection: 2,
  hover: 1.5,
  preview: 1.5,
  violation: 2,
};

const TONE_OPACITY: Readonly<Record<OutlineTone, number>> = {
  selection: 1,
  hover: 0.7,
  preview: 0.9,
  violation: 1,
};

/**
 * Re-render when the theme flips. The shared `Color` objects are mutated in
 * place (so materials need no help), but drei's `Line` takes a colour *prop*,
 * so this component tree does need to hear about it.
 */
function useThemeTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => watchCanvasTheme(() => setTick((t) => t + 1)), []);
  return tick;
}

function useToneColor(tone: OutlineTone): string {
  const tick = useThemeTick();
  return useMemo(() => {
    const colors = getCanvasThemeColors();
    return (tone === 'violation' ? colors.fail : colors.brand).getStyle();
    // `tick` is the dependency that matters; the colours are mutated in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tone, tick]);
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

export interface OutlinePolylineProps {
  /** Plan points, integer mm. Memoise on the caller's side. */
  pointsMm: readonly Pt[];
  elevationMm?: number | undefined;
  tone?: OutlineTone | undefined;
  /** Join the last point back to the first. */
  closed?: boolean | undefined;
  dashed?: boolean | undefined;
  /** Override the tone's width, in CSS pixels. */
  lineWidth?: number | undefined;
  layer?: CanvasLayer | undefined;
  visible?: boolean | undefined;
}

/**
 * A constant-pixel-width outline through plan points. The building block for
 * every highlight: a wall's rectangle, a room's boundary, a rubber-band
 * preview.
 */
export function OutlinePolyline({
  pointsMm,
  elevationMm = 0,
  tone = 'selection',
  closed = false,
  dashed = false,
  lineWidth,
  layer = 'selection',
  visible = true,
}: OutlinePolylineProps): JSX.Element | null {
  const color = useToneColor(tone);
  const points = useMemo(
    () => pointsMmToWorld(pointsMm, elevationMm, closed),
    [pointsMm, elevationMm, closed],
  );
  if (points.length < 2) return null;

  return (
    <Line
      points={points}
      color={color}
      lineWidth={lineWidth ?? TONE_LINE_WIDTH[tone]}
      transparent
      opacity={TONE_OPACITY[tone]}
      dashed={dashed}
      // Spread rather than `dashSize={undefined}`: `exactOptionalPropertyTypes`
      // is on, and an explicit undefined is not the same as an absent prop.
      {...(dashed ? { dashSize: 0.12, gapSize: 0.08 } : {})}
      // Overlays sit on top of the drawing in the plan view; in 3D the layer
      // table restores depth testing (see `depthTestForMode`).
      depthTest={false}
      renderOrder={LAYER_RENDER_ORDER[layer]}
      visible={visible}
    />
  );
}

export interface OutlineFillProps {
  polygonMm: Polygon;
  elevationMm?: number | undefined;
  tone?: OutlineTone | undefined;
  layer?: CanvasLayer | undefined;
  visible?: boolean | undefined;
}

/** The translucent wash inside a selected room or footprint. */
export function OutlineFill({
  polygonMm,
  elevationMm = 0,
  tone = 'selection',
  layer = 'selection',
  visible = true,
}: OutlineFillProps): JSX.Element | null {
  const geometry = useMemo(
    () => polygonFillGeometry(polygonMm, elevationMm),
    [polygonMm, elevationMm],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);

  const materials = getCanvasMaterials();
  const material =
    tone === 'hover'
      ? materials.hoverFill
      : tone === 'preview'
        ? materials.previewFill
        : tone === 'violation'
          ? materials.violationFill
          : materials.selectionFill;

  if (polygonMm.length < 3) return null;

  return (
    <mesh
      geometry={geometry}
      material={material}
      renderOrder={LAYER_RENDER_ORDER[layer]}
      visible={visible}
    />
  );
}

export interface OutlinePolygonProps extends OutlineFillProps {
  /** Draw the wash as well as the edge. Default true. */
  fill?: boolean | undefined;
  lineWidth?: number | undefined;
}

/** Fill plus edge — the default "this element is selected" treatment. */
export function OutlinePolygon({
  polygonMm,
  elevationMm = 0,
  tone = 'selection',
  layer = 'selection',
  fill = true,
  lineWidth,
  visible = true,
}: OutlinePolygonProps): JSX.Element {
  return (
    <>
      {fill ? (
        <OutlineFill
          polygonMm={polygonMm}
          elevationMm={elevationMm}
          tone={tone}
          layer={layer}
          visible={visible}
        />
      ) : null}
      <OutlinePolyline
        pointsMm={polygonMm}
        elevationMm={elevationMm}
        tone={tone}
        layer={layer}
        closed
        lineWidth={lineWidth}
        visible={visible}
      />
    </>
  );
}

export interface OutlineBoxProps {
  boxMm: Bbox;
  elevationMm?: number | undefined;
  tone?: OutlineTone | undefined;
  dashed?: boolean | undefined;
  layer?: CanvasLayer | undefined;
  visible?: boolean | undefined;
}

/** A rectangle: marquee rubber-band, zoom-to-selection preview, ghost bounds. */
export function OutlineBox({
  boxMm,
  elevationMm = 0,
  tone = 'preview',
  dashed = true,
  layer = 'preview',
  visible = true,
}: OutlineBoxProps): JSX.Element {
  const ring = useMemo(() => bboxRingMm(boxMm), [boxMm]);
  return (
    <OutlinePolyline
      pointsMm={ring}
      elevationMm={elevationMm}
      tone={tone}
      dashed={dashed}
      layer={layer}
      closed
      visible={visible}
    />
  );
}
