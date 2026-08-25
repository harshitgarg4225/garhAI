/**
 * The plan's own materials — one shared set, created once, themed in place.
 *
 * The canvas core owns the *selection* treatment (`getCanvasMaterials`); this
 * owns the drawing itself: wall poché, room wash, opening reveals, symbol
 * hairlines, the tool preview.
 *
 * §14: these are module singletons and are MUTATED on a theme change rather
 * than recreated. A new material means a new shader program compile on the next
 * frame, and a shader compile in the middle of a drag is a dropped frame you
 * cannot get back. Colours come from the design tokens through the core's
 * `readTokenColor`, so light/dark is one CSS variable away and never a second
 * palette living in TypeScript.
 */

import { DoubleSide, LineBasicMaterial, MeshBasicMaterial } from 'three';

import { readTokenColor } from '../../../features/canvas/core';

export interface PlanMaterials {
  /** Wall poché — the solid fill an architect reads as "wall". */
  readonly wallFill: MeshBasicMaterial;
  /** Room wash, very light: it must not fight the walls for attention. */
  readonly roomFill: MeshBasicMaterial;
  /** The reveal inside an opening: paper, so the wall reads as interrupted. */
  readonly openingFill: MeshBasicMaterial;
  /** Balcony slab, lighter than a room. */
  readonly balconyFill: MeshBasicMaterial;
  /** Stair and column poché. */
  readonly structureFill: MeshBasicMaterial;
  /** Jambs, door leaves, glazing, treads — the 1px symbol linework. */
  readonly symbolLine: LineBasicMaterial;
  /** Wall outline, drawn over the poché so joints read cleanly. */
  readonly wallLine: LineBasicMaterial;
  /** The tool preview's rubber band. */
  readonly previewLine: LineBasicMaterial;
  /** The snap marker and the crosshair. */
  readonly snapLine: LineBasicMaterial;
}

let cache: PlanMaterials | null = null;

function build(): PlanMaterials {
  const ink = readTokenColor('--garh-ink');
  const inkSubtle = readTokenColor('--garh-ink-subtle');
  const line = readTokenColor('--garh-line');
  const paper = readTokenColor('--garh-surface-sunken');
  const brand = readTokenColor('--garh-brand');

  return {
    wallFill: new MeshBasicMaterial({
      color: ink.clone(),
      side: DoubleSide,
      transparent: true,
      // Not 1.0: at 0.88 a wall under a selection wash still reads as a wall,
      // and overlapping poché from two coplanar walls does not go pure black.
      opacity: 0.88,
      depthWrite: false,
    }),
    roomFill: new MeshBasicMaterial({
      color: brand.clone(),
      side: DoubleSide,
      transparent: true,
      opacity: 0.06,
      depthWrite: false,
    }),
    openingFill: new MeshBasicMaterial({
      color: paper.clone(),
      side: DoubleSide,
      transparent: true,
      opacity: 1,
      depthWrite: false,
    }),
    balconyFill: new MeshBasicMaterial({
      color: inkSubtle.clone(),
      side: DoubleSide,
      transparent: true,
      opacity: 0.1,
      depthWrite: false,
    }),
    structureFill: new MeshBasicMaterial({
      color: inkSubtle.clone(),
      side: DoubleSide,
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
    }),
    symbolLine: new LineBasicMaterial({
      color: ink.clone(),
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
    }),
    wallLine: new LineBasicMaterial({
      color: line.clone(),
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
    }),
    previewLine: new LineBasicMaterial({
      color: brand.clone(),
      transparent: true,
      opacity: 0.95,
      depthWrite: false,
    }),
    snapLine: new LineBasicMaterial({
      color: brand.clone(),
      transparent: true,
      opacity: 0.6,
      depthWrite: false,
    }),
  };
}

export function getPlanMaterials(): PlanMaterials {
  if (cache === null) cache = build();
  return cache;
}

/** Re-read the tokens into the existing materials. Called on a theme change. */
export function refreshPlanMaterials(): void {
  if (cache === null) return;
  const ink = readTokenColor('--garh-ink');
  const inkSubtle = readTokenColor('--garh-ink-subtle');
  const line = readTokenColor('--garh-line');
  const paper = readTokenColor('--garh-surface-sunken');
  const brand = readTokenColor('--garh-brand');

  cache.wallFill.color.copy(ink);
  cache.roomFill.color.copy(brand);
  cache.openingFill.color.copy(paper);
  cache.balconyFill.color.copy(inkSubtle);
  cache.structureFill.color.copy(inkSubtle);
  cache.symbolLine.color.copy(ink);
  cache.wallLine.color.copy(line);
  cache.previewLine.color.copy(brand);
  cache.snapLine.color.copy(brand);
}

/** Release GPU resources. Paired with the core's `disposeCanvasMaterials`. */
export function disposePlanMaterials(): void {
  if (cache === null) return;
  for (const material of Object.values(cache)) material.dispose();
  cache = null;
}
