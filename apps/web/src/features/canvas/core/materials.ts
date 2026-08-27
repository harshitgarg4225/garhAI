/**
 * materials.ts — the shared selection/highlight materials, and the bridge from
 * the design tokens to the GPU.
 *
 * WHY SINGLETONS. Every wall, room, opening and dimension that can be selected
 * would otherwise create its own `LineBasicMaterial`. Each distinct material is
 * a shader program permutation and a uniform upload; a plan with 400 elements
 * and per-element materials breaks batching completely and blows the §14 frame
 * budget before a single pixel is drawn. Here there is one selection material
 * for the whole scene, and switching a colour is a uniform write on an existing
 * program — no recompile.
 *
 * WHY TOKENS RATHER THAN HEX. `@garh/ui/tokens.css` is where light and dark and
 * the WCAG-AA contrast decisions live. If the canvas hard-codes orange, dark
 * mode gets a canvas that ignores it and a selection colour that drifts from
 * every chip and button around it. `refreshCanvasTheme()` re-reads the tokens
 * in place, so a theme toggle recolours the drawing without rebuilding
 * anything.
 *
 * COLOUR SPACE. Tokens are sRGB triplets ("194 65 12"). `Color.setStyle` runs
 * them through Three's colour management, so what lands in the shader is linear
 * and matches the DOM chip next to it instead of being visibly darker.
 */

import { Color, DoubleSide, LineBasicMaterial, MeshBasicMaterial } from 'three';

// ---------------------------------------------------------------------------
// Tokens
// ---------------------------------------------------------------------------

/** Fallbacks matter: tests and SSR have no computed style to read. */
const TOKEN_FALLBACKS: Readonly<Record<string, string>> = {
  '--garh-brand': '194 65 12',
  '--garh-brand-soft': '255 237 227',
  '--garh-fail': '185 28 28',
  '--garh-warn': '180 83 9',
  '--garh-pass': '4 120 87',
  '--garh-focus': '29 78 216',
  '--garh-ink': '28 25 23',
  '--garh-ink-subtle': '109 103 98',
  '--garh-line': '231 229 228',
  '--garh-line-strong': '208 203 199',
  '--garh-surface': '255 255 255',
  '--garh-surface-sunken': '237 235 233',
};

/**
 * Read one `--garh-*` token as a Three colour. The token value is a bare
 * "R G B" triple (that is the shape Tailwind's `rgb(var(--x) / <alpha>)` needs),
 * so it is wrapped before parsing.
 */
export function readTokenColor(name: string, out: Color = new Color()): Color {
  let value = TOKEN_FALLBACKS[name] ?? '0 0 0';
  if (typeof document !== 'undefined' && typeof getComputedStyle === 'function') {
    const read = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (read !== '') value = read;
  }
  // Both "194 65 12" and an author-supplied "#c2410c" survive this.
  out.setStyle(value.startsWith('#') || value.startsWith('rgb') ? value : `rgb(${value})`);
  return out;
}

/** The colours the canvas reads from the design system, resolved once. */
export interface CanvasThemeColors {
  readonly brand: Color;
  readonly brandSoft: Color;
  readonly fail: Color;
  readonly warn: Color;
  readonly pass: Color;
  readonly ink: Color;
  readonly inkSubtle: Color;
  readonly gridLine: Color;
  readonly gridEmphasis: Color;
  readonly paper: Color;
}

let themeColors: CanvasThemeColors | null = null;

export function getCanvasThemeColors(): CanvasThemeColors {
  if (themeColors === null) {
    themeColors = {
      brand: new Color(),
      brandSoft: new Color(),
      fail: new Color(),
      warn: new Color(),
      pass: new Color(),
      ink: new Color(),
      inkSubtle: new Color(),
      gridLine: new Color(),
      gridEmphasis: new Color(),
      paper: new Color(),
    };
    readThemeInto(themeColors);
  }
  return themeColors;
}

function readThemeInto(colors: CanvasThemeColors): void {
  readTokenColor('--garh-brand', colors.brand);
  readTokenColor('--garh-brand-soft', colors.brandSoft);
  readTokenColor('--garh-fail', colors.fail);
  readTokenColor('--garh-warn', colors.warn);
  readTokenColor('--garh-pass', colors.pass);
  readTokenColor('--garh-ink', colors.ink);
  readTokenColor('--garh-ink-subtle', colors.inkSubtle);
  readTokenColor('--garh-line', colors.gridLine);
  readTokenColor('--garh-line-strong', colors.gridEmphasis);
  readTokenColor('--garh-surface-sunken', colors.paper);
}

// ---------------------------------------------------------------------------
// Materials
// ---------------------------------------------------------------------------

/**
 * The shared set. Every module that draws a selection, a hover, a preview or a
 * violation uses these — which is what makes "selected" look the same on a wall
 * and on a piece of furniture without anyone coordinating.
 */
// eslint-disable-next-line @typescript-eslint/consistent-type-definitions -- a type alias (not interface) carries the implicit index signature Object.values() needs to type the dispose loop below
export type CanvasMaterials = {
  /** Outline of the current selection. */
  readonly selectionLine: LineBasicMaterial;
  /** Outline under the cursor. */
  readonly hoverLine: LineBasicMaterial;
  /** Tint inside a selected room/footprint. */
  readonly selectionFill: MeshBasicMaterial;
  readonly hoverFill: MeshBasicMaterial;
  /** The rubber-band geometry a tool is drawing but has not committed. */
  readonly previewLine: LineBasicMaterial;
  readonly previewFill: MeshBasicMaterial;
  /** An element a compliance chip is pointing at. */
  readonly violationLine: LineBasicMaterial;
  readonly violationFill: MeshBasicMaterial;
  /** Drag handles: light body, brand ring. */
  readonly handleFill: MeshBasicMaterial;
};

let materials: CanvasMaterials | null = null;

/**
 * The shared materials, created on first use.
 *
 * NOTE ON LINE WIDTH: WebGL ignores `LineBasicMaterial.linewidth` — every line
 * is 1 device pixel. That is right for hairline geometry, and wrong for a
 * selection outline you are meant to notice, which is why `outline.tsx` draws
 * selection with drei's `<Line>` (a screen-space-width quad strip) and keeps
 * these for the cheap 1 px cases.
 */
export function getCanvasMaterials(): CanvasMaterials {
  if (materials !== null) return materials;
  const c = getCanvasThemeColors();

  materials = {
    selectionLine: new LineBasicMaterial({
      color: c.brand.clone(),
      transparent: true,
      opacity: 1,
      depthWrite: false,
    }),
    hoverLine: new LineBasicMaterial({
      color: c.brand.clone(),
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
    }),
    selectionFill: new MeshBasicMaterial({
      color: c.brand.clone(),
      transparent: true,
      opacity: 0.18,
      depthWrite: false,
      side: DoubleSide,
    }),
    hoverFill: new MeshBasicMaterial({
      color: c.brand.clone(),
      transparent: true,
      opacity: 0.09,
      depthWrite: false,
      side: DoubleSide,
    }),
    previewLine: new LineBasicMaterial({
      color: c.brand.clone(),
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
    }),
    previewFill: new MeshBasicMaterial({
      color: c.brand.clone(),
      transparent: true,
      opacity: 0.12,
      depthWrite: false,
      side: DoubleSide,
    }),
    violationLine: new LineBasicMaterial({
      color: c.fail.clone(),
      transparent: true,
      opacity: 1,
      depthWrite: false,
    }),
    violationFill: new MeshBasicMaterial({
      color: c.fail.clone(),
      transparent: true,
      opacity: 0.16,
      depthWrite: false,
      side: DoubleSide,
    }),
    handleFill: new MeshBasicMaterial({
      color: c.paper.clone(),
      transparent: false,
      depthWrite: false,
      side: DoubleSide,
    }),
  };
  return materials;
}

/**
 * Re-read the tokens and recolour in place. No material is replaced, so no
 * shader is recompiled and nothing that referenced a material has to be told.
 */
export function refreshCanvasTheme(): void {
  if (themeColors !== null) readThemeInto(themeColors);
  if (materials === null) return;
  const c = getCanvasThemeColors();
  materials.selectionLine.color.copy(c.brand);
  materials.hoverLine.color.copy(c.brand);
  materials.selectionFill.color.copy(c.brand);
  materials.hoverFill.color.copy(c.brand);
  materials.previewLine.color.copy(c.brand);
  materials.previewFill.color.copy(c.brand);
  materials.violationLine.color.copy(c.fail);
  materials.violationFill.color.copy(c.fail);
  materials.handleFill.color.copy(c.paper);
}

/**
 * Watch for a theme change and recolour. `@garh/ui`'s `applyTheme` writes both
 * a class and `data-theme` on `<html>`; either one firing is enough.
 *
 * Returns a cleanup function, and `onChange` lets `CanvasRoot` ask for a frame
 * (the canvas is `frameloop="demand"`, so a recolour with no invalidate would
 * not be visible until the next interaction).
 */
export function watchCanvasTheme(onChange?: () => void): () => void {
  if (typeof MutationObserver !== 'function' || typeof document === 'undefined') {
    return () => undefined;
  }
  const observer = new MutationObserver(() => {
    refreshCanvasTheme();
    onChange?.();
  });
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'data-theme'],
  });
  return () => {
    observer.disconnect();
  };
}

/** Release GPU resources. `CanvasRoot` calls this when the last canvas unmounts. */
export function disposeCanvasMaterials(): void {
  if (materials === null) return;
  for (const material of Object.values(materials)) material.dispose();
  materials = null;
  themeColors = null;
}
