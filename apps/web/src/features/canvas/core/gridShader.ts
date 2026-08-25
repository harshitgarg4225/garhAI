/**
 * gridShader.ts — the drafting grid, as one quad and one shader.
 *
 * WHY A SHADER AND NOT LINES. A 30 × 40 m plot on the 115 mm module is roughly
 * 260 × 350 grid lines. As React nodes that is ~600 components and ~600 draw
 * calls per frame; as a `LineSegments` buffer it is one draw call but a buffer
 * that has to be rebuilt every time the camera moves far enough, and it aliases
 * badly at any zoom where lines land between pixels. One screen-covering quad
 * with an analytic grid is a single draw call that never rebuilds, is
 * pixel-exact at every zoom, and antialiases itself.
 *
 * WHY `fwidth` AND NOT `mmPerPx`. In the orthographic view the scale is
 * constant, so the fade could be computed on the CPU. In the perspective view
 * it is not: the grid recedes, and a CPU-computed fade would either alias in
 * the distance or wash out nearby. `fwidth` gives each fragment its own local
 * scale, so the same shader behaves correctly in both views — which is the
 * point of having one scene graph.
 *
 * THREE LEVELS, EACH FADED BY ITS OWN SPACING:
 *   fine (25 mm)     — only while the fine-grid toggle (G) is on
 *   module (115 mm)  — the half-brick the solver and the snap both use
 *   emphasis (1 m)   — the heavy line, so you can count metres at a glance
 * A level fades out once its lines are closer than `GRID_MIN_SPACING_PX` apart,
 * because below that a grid stops being a grid and becomes grey.
 *
 * Plus the plot origin axes (model x = 0 and y = 0), drawn slightly heavier:
 * free orientation, and it is where every dimension chain starts.
 */

import { DoubleSide, ShaderMaterial, type Color } from 'three';

import {
  GRID_EMPHASIS_MM,
  GRID_FINE_MM,
  GRID_FULL_SPACING_PX,
  GRID_LINE_WIDTH_PX,
  GRID_MIN_SPACING_PX,
  GRID_MODULE_MM,
  MM_PER_WORLD_UNIT,
} from './constants';
import { getCanvasThemeColors } from './materials';

const VERTEX_SHADER = /* glsl */ `
varying vec3 vWorldPosition;

void main() {
  vec4 worldPosition = modelMatrix * vec4(position, 1.0);
  vWorldPosition = worldPosition.xyz;
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
}
`;

const FRAGMENT_SHADER = /* glsl */ `
precision highp float;

varying vec3 vWorldPosition;

uniform float uMmPerWorldUnit;
uniform float uFineMm;
uniform float uModuleMm;
uniform float uEmphasisMm;
uniform float uFineOn;
uniform float uLineWidthPx;
uniform float uMinSpacingPx;
uniform float uFullSpacingPx;
uniform float uOpacity;
uniform vec3 uLineColor;
uniform vec3 uEmphasisColor;
uniform vec3 uAxisColor;

/**
 * Coverage of the nearest grid line at this fragment, 0..1, antialiased by the
 * screen-space derivative. Also reports how far apart the lines are in pixels,
 * which is what the fade uses.
 */
vec2 gridCoverage(vec2 mmCoord, float stepMm) {
  vec2 coord = mmCoord / stepMm;
  vec2 deriv = max(fwidth(coord), vec2(1e-8));
  // Distance to the nearest line, in pixels, per axis.
  vec2 distPx = abs(fract(coord - 0.5) - 0.5) / deriv;
  float nearestPx = min(distPx.x, distPx.y);
  float coverage = 1.0 - clamp(nearestPx / uLineWidthPx, 0.0, 1.0);
  float spacingPx = 1.0 / max(deriv.x, deriv.y);
  return vec2(coverage, spacingPx);
}

/** A single line at coordinate 0 on one axis — the plot origin axes. */
float axisCoverage(float mmValue, float widthPx) {
  float deriv = max(fwidth(mmValue), 1e-8);
  float distPx = abs(mmValue) / deriv;
  return 1.0 - clamp(distPx / widthPx, 0.0, 1.0);
}

void main() {
  // World (Y-up, metres) back to model millimetres. North is -Z, so the sign
  // flip here is the same one coords.ts applies on the CPU.
  vec2 mmCoord = vec2(vWorldPosition.x, -vWorldPosition.z) * uMmPerWorldUnit;

  vec3 color = uLineColor;
  float alpha = 0.0;

  if (uFineOn > 0.5) {
    vec2 fine = gridCoverage(mmCoord, uFineMm);
    float fade = smoothstep(uMinSpacingPx, uFullSpacingPx, fine.y);
    alpha = max(alpha, fine.x * fade * 0.5);
  }

  vec2 module_ = gridCoverage(mmCoord, uModuleMm);
  float moduleFade = smoothstep(uMinSpacingPx, uFullSpacingPx, module_.y);
  alpha = max(alpha, module_.x * moduleFade * 0.85);

  vec2 emphasis = gridCoverage(mmCoord, uEmphasisMm);
  float emphasisFade = smoothstep(uMinSpacingPx, uFullSpacingPx, emphasis.y);
  float emphasisAlpha = emphasis.x * emphasisFade;
  if (emphasisAlpha > 0.0) {
    color = mix(color, uEmphasisColor, emphasisAlpha);
    alpha = max(alpha, emphasisAlpha);
  }

  float axis = max(axisCoverage(mmCoord.x, uLineWidthPx * 1.5),
                   axisCoverage(mmCoord.y, uLineWidthPx * 1.5));
  if (axis > 0.0) {
    color = mix(color, uAxisColor, axis);
    alpha = max(alpha, axis * 0.9);
  }

  if (alpha <= 0.001) discard;

  gl_FragColor = vec4(color, alpha * uOpacity);

  #include <colorspace_fragment>
}
`;

export interface GridUniformValues {
  /** Show the 25 mm fine grid (the G toggle). */
  readonly fine?: boolean;
  /** Overall grid strength. Lowered in 3D, where the grid is context not paper. */
  readonly opacity?: number;
}

/**
 * Build the grid material. One per `<Grid>`; the uniforms are mutated in place
 * afterwards, never replaced, so the program is compiled exactly once.
 */
export function createGridMaterial(values: GridUniformValues = {}): ShaderMaterial {
  const colors = getCanvasThemeColors();
  return new ShaderMaterial({
    vertexShader: VERTEX_SHADER,
    fragmentShader: FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    // The grid is under everything, and in 3D it should not z-fight the ground
    // slab it sits on — it is drawn first and never writes depth.
    depthTest: true,
    side: DoubleSide,
    // No `extensions: { derivatives }` declaration: three r163 dropped WebGL1,
    // and `fwidth` is core in WebGL2/GLSL ES 3.0.
    uniforms: {
      uMmPerWorldUnit: { value: MM_PER_WORLD_UNIT },
      uFineMm: { value: GRID_FINE_MM },
      uModuleMm: { value: GRID_MODULE_MM },
      uEmphasisMm: { value: GRID_EMPHASIS_MM },
      uFineOn: { value: values.fine === true ? 1 : 0 },
      uLineWidthPx: { value: GRID_LINE_WIDTH_PX },
      uMinSpacingPx: { value: GRID_MIN_SPACING_PX },
      uFullSpacingPx: { value: GRID_FULL_SPACING_PX },
      uOpacity: { value: values.opacity ?? 1 },
      uLineColor: { value: colors.gridLine.clone() },
      uEmphasisColor: { value: colors.gridEmphasis.clone() },
      uAxisColor: { value: colors.inkSubtle.clone() },
    },
  });
}

/** Update a grid material in place. No reallocation, no shader recompile. */
export function updateGridMaterial(material: ShaderMaterial, values: GridUniformValues): void {
  const uniforms = material.uniforms;
  if (values.fine !== undefined && uniforms.uFineOn !== undefined) {
    uniforms.uFineOn.value = values.fine ? 1 : 0;
  }
  if (values.opacity !== undefined && uniforms.uOpacity !== undefined) {
    uniforms.uOpacity.value = values.opacity;
  }
}

/** Re-read the theme tokens into an existing grid material. */
export function refreshGridMaterialTheme(material: ShaderMaterial): void {
  const colors = getCanvasThemeColors();
  const line = material.uniforms.uLineColor?.value as Color | undefined;
  const emphasis = material.uniforms.uEmphasisColor?.value as Color | undefined;
  const axis = material.uniforms.uAxisColor?.value as Color | undefined;
  line?.copy(colors.gridLine);
  emphasis?.copy(colors.gridEmphasis);
  axis?.copy(colors.inkSubtle);
}
