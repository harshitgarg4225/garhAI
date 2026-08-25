/**
 * Parametric box proxies — **not modelled furniture.**
 *
 * Read this paragraph before using anything below. Garh AI has NO authored 3D
 * furniture assets. `CatalogueItem.assetUrl` is `null` for all 45 seeded items
 * and there is no mesh file anywhere in this repo. What this module produces is
 * a handful of axis-aligned boxes per item, derived arithmetically from the
 * item's three catalogue dimensions — enough for a plan to read correctly, for
 * a 3D view to show that *something* of the right size is there, and for a
 * depth/MLSD ControlNet pass to have geometry to condition on. It is not enough
 * for an interior render anyone would show a client, and the playbook is right
 * that real assets are required before Phase 7 interiors mean anything.
 *
 * Every proxy carries `catalogId` and `source: 'parametric-box-proxy'`. That is
 * the swap seam: when real meshes land, a loader keyed on `catalogId` replaces
 * the boxes and NOTHING in placement, collision, ops or the browser changes,
 * because none of them import this file. The renderer does, and only to decide
 * what to draw.
 *
 * ## Local frame
 *
 * Same as `types.ts`: X = width, Y = depth (+Y is the front), Z = height above
 * the storey floor. Origin at the footprint centre, on the floor. Integer mm
 * throughout — a proxy is derived from catalogue integers by integer division,
 * so two runs produce byte-identical boxes.
 */

import type { CatalogueItem, FurnitureCategory } from './types';

/** One box of a proxy, in the item's local millimetre frame. */
export interface ProxyBox {
  /** Stable within an item: 'body', 'back', 'headboard', 'cabin'… */
  readonly key: string;
  /** Centre of the box, local mm. `z` is measured up from the floor. */
  readonly cx: number;
  readonly cy: number;
  readonly cz: number;
  /** Full extents, local mm, all ≥ 1. */
  readonly wMm: number;
  readonly dMm: number;
  readonly hMm: number;
}

export interface BoxProxy {
  /** THE swap tag. A real-asset loader keys on this and ignores the boxes. */
  readonly catalogId: string;
  /** Always this string today. Grep for it before believing any render. */
  readonly source: 'parametric-box-proxy';
  readonly boxes: readonly ProxyBox[];
  /**
   * Actual extents of the boxes, measured — not copied from the catalogue.
   *
   * `widthMm` and `depthMm` always equal the catalogue values: the plan
   * footprint is what collision, clearance and the drawing set all depend on,
   * and a proxy that changed it would be lying about the thing that matters.
   *
   * `heightMm` can EXCEED the catalogue height, and does for three recipes. A
   * bed's catalogue height (600 mm) is its mattress top, not its headboard; a
   * kitchen counter's (900 mm) is the worktop, not the splashback; a WC's is
   * the seat, not the cistern. The catalogue measures the working surface; the
   * proxy draws the object. Anything computing a bounding volume should read
   * this number rather than assume the catalogue's.
   */
  readonly widthMm: number;
  readonly depthMm: number;
  readonly heightMm: number;
}

/** Integer halving that never drifts: floor for the low side, remainder for the high. */
function half(n: number): number {
  return Math.trunc(n / 2);
}

function box(
  key: string,
  cx: number,
  cy: number,
  cz: number,
  wMm: number,
  dMm: number,
  hMm: number,
): ProxyBox {
  return {
    key,
    cx: Math.trunc(cx),
    cy: Math.trunc(cy),
    cz: Math.trunc(cz),
    wMm: Math.max(1, Math.trunc(wMm)),
    dMm: Math.max(1, Math.trunc(dMm)),
    hMm: Math.max(1, Math.trunc(hMm)),
  };
}

/** A single box filling the whole envelope — the honest default. */
function solid(item: CatalogueItem): ProxyBox[] {
  return [box('body', 0, 0, half(item.heightMm), item.widthMm, item.depthMm, item.heightMm)];
}

/**
 * Bed: a low mattress slab plus a headboard at the BACK (−Y), because the front
 * (+Y) is where the clearance strip goes and a headboard there would contradict
 * the access rectangle the collision pass draws.
 */
function bed(item: CatalogueItem): ProxyBox[] {
  const headboard = Math.min(120, Math.max(60, half(item.depthMm) >> 3));
  const mattressDepth = item.depthMm - headboard;
  return [
    box(
      'mattress',
      0,
      half(headboard),
      half(item.heightMm),
      item.widthMm,
      mattressDepth,
      item.heightMm,
    ),
    box(
      'headboard',
      0,
      -half(item.depthMm) + half(headboard),
      half(item.heightMm) + 200,
      item.widthMm,
      headboard,
      item.heightMm + 400,
    ),
  ];
}

/** Sofa / armchair / diwan: seat slab, back at −Y, two arms. */
function seating(item: CatalogueItem): ProxyBox[] {
  const backDepth = Math.min(200, Math.max(80, half(item.depthMm) >> 1));
  const armWidth = Math.min(180, Math.max(60, half(item.widthMm) >> 2));
  const seatH = Math.max(150, half(item.heightMm));
  const seatDepth = item.depthMm - backDepth;
  const seatWidth = Math.max(1, item.widthMm - 2 * armWidth);
  const backY = -half(item.depthMm) + half(backDepth);
  return [
    box('seat', 0, half(backDepth), half(seatH), seatWidth, seatDepth, seatH),
    box('back', 0, backY, half(item.heightMm), item.widthMm, backDepth, item.heightMm),
    box(
      'arm-l',
      -half(item.widthMm) + half(armWidth),
      half(backDepth),
      half(seatH) + 60,
      armWidth,
      seatDepth,
      seatH + 120,
    ),
    box(
      'arm-r',
      half(item.widthMm) - half(armWidth),
      half(backDepth),
      half(seatH) + 60,
      armWidth,
      seatDepth,
      seatH + 120,
    ),
  ];
}

/** Table / desk: a top slab on a recessed plinth, so a plan reads the overhang. */
function table(item: CatalogueItem): ProxyBox[] {
  const topThickness = Math.min(60, Math.max(20, item.heightMm >> 4));
  const inset = Math.min(120, Math.max(40, half(Math.min(item.widthMm, item.depthMm)) >> 2));
  const legH = Math.max(1, item.heightMm - topThickness);
  return [
    box('top', 0, 0, item.heightMm - half(topThickness), item.widthMm, item.depthMm, topThickness),
    box(
      'base',
      0,
      0,
      half(legH),
      Math.max(1, item.widthMm - 2 * inset),
      Math.max(1, item.depthMm - 2 * inset),
      legH,
    ),
  ];
}

/** Counter: worktop plus a splashback along the back edge. */
function counter(item: CatalogueItem): ProxyBox[] {
  const splash = Math.min(60, Math.max(20, item.depthMm >> 4));
  return [
    box('carcass', 0, 0, half(item.heightMm), item.widthMm, item.depthMm, item.heightMm),
    box(
      'splashback',
      0,
      -half(item.depthMm) + half(splash),
      item.heightMm + 200,
      item.widthMm,
      splash,
      400,
    ),
  ];
}

/** WC: bowl plus cistern at the back. Washbasins and tubs stay a single box. */
function sanitary(item: CatalogueItem): ProxyBox[] {
  if (!item.id.startsWith('wc-')) return solid(item);
  const cistern = Math.min(200, Math.max(80, item.depthMm >> 2));
  return [
    box(
      'bowl',
      0,
      half(cistern),
      half(item.heightMm),
      item.widthMm,
      Math.max(1, item.depthMm - cistern),
      item.heightMm,
    ),
    box(
      'cistern',
      0,
      -half(item.depthMm) + half(cistern),
      half(item.heightMm) + 100,
      item.widthMm,
      cistern,
      item.heightMm,
    ),
  ];
}

/** Car / two-wheeler: body slab with a cabin box, so a stilt plan reads as parking. */
function vehicle(item: CatalogueItem): ProxyBox[] {
  const bodyH = Math.max(200, half(item.heightMm));
  const cabinD = Math.max(1, half(item.depthMm));
  const cabinW = Math.max(1, item.widthMm - 200);
  return [
    box('body', 0, 0, half(bodyH), item.widthMm, item.depthMm, bodyH),
    box(
      'cabin',
      0,
      -Math.trunc(item.depthMm / 8),
      bodyH + half(Math.max(1, item.heightMm - bodyH)),
      cabinW,
      cabinD,
      Math.max(1, item.heightMm - bodyH),
    ),
  ];
}

const RECIPES: Readonly<Record<FurnitureCategory, (item: CatalogueItem) => ProxyBox[]>> = {
  bed,
  seating,
  table,
  storage: solid,
  kitchen: counter,
  sanitary,
  appliance: solid,
  vehicle,
  service: solid,
  other: solid,
};

/**
 * The proxy for one catalogue item. Deterministic and cheap — but memoised by
 * {@link proxyCache} anyway, because the renderer asks for it once per item per
 * rebuild and there is no reason to redo integer arithmetic 45 times a frame.
 */
export function boxProxyFor(item: CatalogueItem): BoxProxy {
  const recipe = RECIPES[item.category];
  // A recipe that produced nothing would render an invisible item; the solid
  // fallback guarantees every catalogue entry has something on screen.
  const built = recipe(item);
  const boxes = built.length > 0 ? built : solid(item);

  let top = 0;
  for (const box of boxes) top = Math.max(top, box.cz + Math.trunc(box.hMm / 2));

  return {
    catalogId: item.id,
    source: 'parametric-box-proxy',
    boxes,
    widthMm: item.widthMm,
    depthMm: item.depthMm,
    heightMm: Math.max(item.heightMm, top),
  };
}

const cache = new Map<string, BoxProxy>();

/** Memoised {@link boxProxyFor}, keyed on catalogue id. */
export function proxyCache(item: CatalogueItem): BoxProxy {
  const hit = cache.get(item.id);
  if (hit !== undefined) return hit;
  const built = boxProxyFor(item);
  cache.set(item.id, built);
  return built;
}

/** Total box count for a set of items — what the renderer sizes its buffers to. */
export function proxyBoxCount(items: Iterable<CatalogueItem>): number {
  let n = 0;
  for (const item of items) n += proxyCache(item).boxes.length;
  return n;
}
