/**
 * overlayMaterials.ts — the overlay layers' shared materials and text config.
 *
 * Same argument as `core/materials.ts`: one material per treatment for the
 * whole scene, recoloured in place when the theme flips, never recreated. A
 * per-label material is a shader permutation per label.
 *
 * These are separate from the core set because they are *annotation* colours —
 * dimension ink, leader hairlines, room-tag text — rather than selection
 * states. The core owns "this thing is selected"; this owns "this is drawn on
 * top of the plan and is meant to be read".
 */

import { Color, LineBasicMaterial, MeshBasicMaterial } from 'three';

import { getCanvasThemeColors, LAYER_RENDER_ORDER, readTokenColor } from '../../core';

export interface OverlayMaterials {
  /** Dimension lines, witness lines and ticks. */
  readonly dimensionLine: LineBasicMaterial;
  /** The dimension string under the pointer or being edited. */
  readonly dimensionActive: LineBasicMaterial;
  /** Room-tag leader lines — lighter than a dimension, they are not measured. */
  readonly leaderLine: LineBasicMaterial;
  /** Invisible pick geometry. Never drawn; exists so a raycast can hit it. */
  readonly pickProxy: MeshBasicMaterial;
  /** The disc behind a compliance marker. */
  readonly markerFail: MeshBasicMaterial;
  readonly markerWarn: MeshBasicMaterial;
}

let materials: OverlayMaterials | null = null;

export function getOverlayMaterials(): OverlayMaterials {
  if (materials !== null) return materials;
  const theme = getCanvasThemeColors();

  materials = {
    dimensionLine: new LineBasicMaterial({
      color: readTokenColor('--garh-ink-subtle', new Color()),
      transparent: true,
      opacity: 0.9,
      depthTest: false,
      depthWrite: false,
    }),
    dimensionActive: new LineBasicMaterial({
      color: theme.brand.clone(),
      transparent: true,
      opacity: 1,
      depthTest: false,
      depthWrite: false,
    }),
    leaderLine: new LineBasicMaterial({
      color: readTokenColor('--garh-ink-subtle', new Color()),
      transparent: true,
      opacity: 0.55,
      depthTest: false,
      depthWrite: false,
    }),
    pickProxy: new MeshBasicMaterial({
      // `visible: false` would take the mesh out of the render list AND out of
      // `Raycaster`'s reach — the core's picker checks `isEffectivelyVisible`.
      // A fully transparent material with `colorWrite` off costs one degenerate
      // draw call and stays pickable, which is the trade we want.
      transparent: true,
      opacity: 0,
      depthTest: false,
      depthWrite: false,
      colorWrite: false,
    }),
    markerFail: new MeshBasicMaterial({
      color: theme.fail.clone(),
      transparent: true,
      opacity: 0.92,
      depthTest: false,
      depthWrite: false,
    }),
    markerWarn: new MeshBasicMaterial({
      color: theme.warn.clone(),
      transparent: true,
      opacity: 0.92,
      depthTest: false,
      depthWrite: false,
    }),
  };
  return materials;
}

/** Re-read the tokens after a theme change. Mutates in place; no recompile. */
export function refreshOverlayMaterials(): void {
  if (materials === null) return;
  const theme = getCanvasThemeColors();
  readTokenColor('--garh-ink-subtle', materials.dimensionLine.color);
  readTokenColor('--garh-ink-subtle', materials.leaderLine.color);
  materials.dimensionActive.color.copy(theme.brand);
  materials.markerFail.color.copy(theme.fail);
  materials.markerWarn.color.copy(theme.warn);
}

export function disposeOverlayMaterials(): void {
  if (materials === null) return;
  for (const material of Object.values(materials)) material.dispose();
  materials = null;
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

/**
 * THE FONT. Read this before shipping.
 *
 * `<Text>` from `@react-three/drei` wraps `troika-three-text` (MIT, already a
 * transitive dependency of drei — no new package). With no `font` prop, troika
 * fetches **Roboto from fonts.gstatic.com at runtime**. That breaks three of
 * this product's constraints at once:
 *
 *   · §13 CSP — the canvas would need a `font-src` exception to a third party.
 *   · "runs with zero API keys, one `docker compose up`" — a studio on a bad
 *     connection gets a canvas with no dimension text.
 *   · privacy — a request to Google on every project open.
 *
 * So the layers take a `fontUrl` and default to a SELF-HOSTED file. That file
 * is an asset, not code, and this module cannot create it:
 *
 *   TODO(integrator): add `apps/web/public/fonts/inter-medium.woff` (Inter is
 *   SIL OFL — permitted) and keep this constant pointing at it.
 *
 * Until it exists, troika logs a load failure and falls back to its bundled
 * default, so development is not blocked — but the fallback is the network
 * path, and shipping without the file is a CSP bug, not a cosmetic one.
 */
export const LABEL_FONT_URL = '/fonts/inter-medium.woff';

/**
 * Nominal font size in LOCAL units for every overlay label.
 *
 * Always 1. Apparent size comes from the parent group's scale
 * (`useScreenScale`), because changing troika's `fontSize` re-runs glyph
 * layout and re-uploads geometry — per label, per zoom frame.
 */
export const LABEL_FONT_SIZE_LOCAL = 1;

/** Draw order for the two overlay families. One table, shared with the core. */
export const DIMENSION_RENDER_ORDER = LAYER_RENDER_ORDER.dimension;
export const ROOM_LABEL_RENDER_ORDER = LAYER_RENDER_ORDER.roomLabel;
export const ANNOTATION_RENDER_ORDER = LAYER_RENDER_ORDER.annotation;
