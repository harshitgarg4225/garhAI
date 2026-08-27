/**
 * geometryBuild.ts — SolidSpecs → merged, pickable vertex buffers.
 *
 * THIS FILE IS THE mm→WORLD DOOR for the 3D synthesis, exactly as
 * `packTriangles` is for the 2D plan: model mm (x east, y north, z elevation)
 * becomes world units here and nowhere else —
 *
 *      worldX = +mmX × WORLD_UNITS_PER_MM
 *      worldY = +mmZ × WORLD_UNITS_PER_MM     (elevation)
 *      worldZ = −mmY × WORLD_UNITS_PER_MM     (north is −Z, per coords.ts)
 *
 * BATCHING (§14): solids are merged into BUCKETS — one draw call per
 * (surface material × glassiness × pick mode) per group, so a G+2 storey is a
 * handful of draw calls, not one per wall. Picking survives batching the same
 * way the 2D plan's does: one pick target per TRIANGLE in a parallel array,
 * `faceTargets[intersection.faceIndex]` is the answer. Boolean-cut walls
 * contribute a variable number of triangles, which is why the array is built
 * per-triangle rather than assuming two triangles per quad.
 *
 * NORMALS: flat-shaded, computed per face. Caps get exact ±Y; extruded sides
 * get their outward normal derived from the CCW footprint winding; Manifold
 * output gets a cross-product normal per triangle. Materials are DoubleSide
 * (winding from three sources is not worth unifying), so normals only feed
 * lighting, never culling.
 *
 * NO GPU OBJECTS HERE: buckets carry plain Float32Arrays. `ThreeDScene`'s
 * leaf components wrap them in BufferGeometry with a disposal effect — that
 * keeps this module testable in node and keeps the per-storey cache free of
 * GPU lifetime headaches (a cached array survives a React StrictMode
 * double-render; a cached BufferGeometry does not survive a dispose).
 */

import { triangulate, type HouseModel, type Polygon, type SurfaceGroup } from '@garh/model';

// Deliberately the leaf modules, not the `../core` barrel: the barrel exports
// React components, and this file must stay importable by node-side specs
// without dragging @react-three/fiber along.
import { WORLD_UNITS_PER_MM } from '../core/constants';
import type { PickTarget } from '../core/pickRegistry';
import type { PrismCutter } from './booleans';
import type { PrismProfileF } from './extrusion';
import { solidsOfGroup, type SolidSpec } from './solids';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One mergeable, pickable mesh worth of data. Plain arrays — no three. */
export interface BuiltBucket {
  /** Stable key inside the group: surface|element-scope|glass|pickmode. */
  readonly key: string;
  readonly surface: SurfaceGroup;
  readonly storeyId: string | null;
  /** Non-null when this bucket is a single element with its own material. */
  readonly elementId: string | null;
  readonly glass: boolean;
  readonly overrideColor: string | null;
  /** True ⇒ resolve picks by point-in-room lookup (floor slabs). */
  readonly pickRoomByPoint: boolean;
  readonly positions: Float32Array;
  readonly normals: Float32Array;
  /** One pick target per triangle; null = registered but not selectable. */
  readonly faceTargets: readonly (PickTarget | null)[];
}

/** One rebuild group, fully built. */
export interface GroupBuild {
  readonly key: string;
  readonly storeyId: string | null;
  readonly buckets: readonly BuiltBucket[];
  /** False when any solid in the group wanted cuts and the engine was absent. */
  readonly holesApplied: boolean;
}

// ---------------------------------------------------------------------------
// Accumulator
// ---------------------------------------------------------------------------

class BucketAccumulator {
  readonly positions: number[] = [];

  readonly normals: number[] = [];

  readonly faceTargets: (PickTarget | null)[] = [];

  constructor(
    readonly key: string,
    readonly surface: SurfaceGroup,
    readonly storeyId: string | null,
    readonly elementId: string | null,
    readonly glass: boolean,
    readonly overrideColor: string | null,
    readonly pickRoomByPoint: boolean,
  ) {}

  /** Push one triangle given in MODEL MM (x, y, zElevation) with its normal. */
  addTriangleMm(
    ax: number,
    ay: number,
    az: number,
    bx: number,
    by: number,
    bz: number,
    cx: number,
    cy: number,
    cz: number,
    nxMm: number,
    nyMm: number,
    nzMm: number,
    target: PickTarget | null,
  ): void {
    const k = WORLD_UNITS_PER_MM;
    // World normal: same axis mapping as positions, then normalised.
    const wnx = nxMm;
    const wny = nzMm;
    const wnz = -nyMm;
    const len = Math.hypot(wnx, wny, wnz) || 1;
    const nx = wnx / len;
    const ny = wny / len;
    const nz = wnz / len;
    this.positions.push(ax * k, az * k, -ay * k, bx * k, bz * k, -by * k, cx * k, cz * k, -cy * k);
    this.normals.push(nx, ny, nz, nx, ny, nz, nx, ny, nz);
    this.faceTargets.push(target);
  }

  toBucket(): BuiltBucket {
    return {
      key: this.key,
      surface: this.surface,
      storeyId: this.storeyId,
      elementId: this.elementId,
      glass: this.glass,
      overrideColor: this.overrideColor,
      pickRoomByPoint: this.pickRoomByPoint,
      positions: new Float32Array(this.positions),
      normals: new Float32Array(this.normals),
      faceTargets: this.faceTargets,
    };
  }
}

// ---------------------------------------------------------------------------
// Prism → triangles
// ---------------------------------------------------------------------------

/**
 * Append an uncut prism: triangulated caps (via the model core's ear-clipper,
 * the same one the area statement uses) plus one quad per footprint edge.
 * The footprint is CCW, so each edge's outward normal is its RIGHT normal.
 */
function appendPrism(
  acc: BucketAccumulator,
  profile: PrismProfileF,
  target: PickTarget | null,
): void {
  const ring = profile.polygon;
  if (ring.length < 3 || profile.topMm <= profile.baseMm) return;

  // triangulate() is typed on the integer Polygon but is pure arithmetic;
  // float points are structurally valid (same convention as planGeometry).
  const tris = triangulate(ring as Polygon);
  for (const [a, b, c] of tris) {
    // Top cap, normal +Z (up).
    acc.addTriangleMm(
      a.x,
      a.y,
      profile.topMm,
      b.x,
      b.y,
      profile.topMm,
      c.x,
      c.y,
      profile.topMm,
      0,
      0,
      1,
      target,
    );
    // Bottom cap, normal −Z (down).
    acc.addTriangleMm(
      a.x,
      a.y,
      profile.baseMm,
      c.x,
      c.y,
      profile.baseMm,
      b.x,
      b.y,
      profile.baseMm,
      0,
      0,
      -1,
      target,
    );
  }

  for (let i = 0; i < ring.length; i += 1) {
    const p = ring[i];
    const q = ring[(i + 1) % ring.length];
    if (p === undefined || q === undefined) continue;
    const dx = q.x - p.x;
    const dy = q.y - p.y;
    if (dx === 0 && dy === 0) continue;
    // CCW ring ⇒ interior on the left ⇒ outward is the right normal.
    const nx = dy;
    const ny = -dx;
    acc.addTriangleMm(
      p.x,
      p.y,
      profile.baseMm,
      q.x,
      q.y,
      profile.baseMm,
      q.x,
      q.y,
      profile.topMm,
      nx,
      ny,
      0,
      target,
    );
    acc.addTriangleMm(
      p.x,
      p.y,
      profile.baseMm,
      q.x,
      q.y,
      profile.topMm,
      p.x,
      p.y,
      profile.topMm,
      nx,
      ny,
      0,
      target,
    );
  }
}

/** Append a Manifold-cut mesh: mm triangles with cross-product flat normals. */
function appendCutMesh(
  acc: BucketAccumulator,
  positionsMm: Float32Array,
  target: PickTarget | null,
): void {
  for (let i = 0; i + 8 < positionsMm.length; i += 9) {
    const ax = positionsMm[i] ?? 0;
    const ay = positionsMm[i + 1] ?? 0;
    const az = positionsMm[i + 2] ?? 0;
    const bx = positionsMm[i + 3] ?? 0;
    const by = positionsMm[i + 4] ?? 0;
    const bz = positionsMm[i + 5] ?? 0;
    const cx = positionsMm[i + 6] ?? 0;
    const cy = positionsMm[i + 7] ?? 0;
    const cz = positionsMm[i + 8] ?? 0;
    // Cross product (b−a) × (c−a), in mm model space (z-up).
    const ux = bx - ax;
    const uy = by - ay;
    const uz = bz - az;
    const vx = cx - ax;
    const vy = cy - ay;
    const vz = cz - az;
    acc.addTriangleMm(
      ax,
      ay,
      az,
      bx,
      by,
      bz,
      cx,
      cy,
      cz,
      uy * vz - uz * vy,
      uz * vx - ux * vz,
      ux * vy - uy * vx,
      target,
    );
  }
}

// ---------------------------------------------------------------------------
// Group building
// ---------------------------------------------------------------------------

function bucketKeyOf(spec: SolidSpec, elementScoped: boolean): string {
  const scope = elementScoped ? (spec.elementId ?? '') : '';
  const override = spec.overrideColor ?? '';
  return `${spec.surface}|${scope}|${spec.glass ? 'g' : 'o'}|${spec.pickRoomByPoint ? 'room' : 'face'}|${override}`;
}

/**
 * Build one rebuild group into its merged buckets.
 *
 * `cutter` null ⇒ the honest no-WASM fallback: every solid renders as its
 * uncut prism and `holesApplied` comes back false so the scene can report it.
 * A per-solid boolean failure also falls back to the uncut prism, without
 * flipping the group flag — the engine works, that one solid degraded.
 *
 * `elementScopedIds` — elements with an element-scoped MaterialAssignment get
 * their own bucket so op 29 can recolour one wall without repainting the
 * merged mesh it would otherwise share.
 */
export function buildGroup(
  house: HouseModel,
  groupKey: string,
  cutter: PrismCutter | null,
  elementScopedIds: ReadonlySet<string>,
): GroupBuild {
  const { storeyId, solids } = solidsOfGroup(house, groupKey);
  const buckets = new Map<string, BucketAccumulator>();
  let wantedCuts = false;
  let missedCuts = false;

  for (const spec of solids) {
    const elementScoped = spec.elementId !== null && elementScopedIds.has(spec.elementId);
    const key = bucketKeyOf(spec, elementScoped);
    let acc = buckets.get(key);
    if (acc === undefined) {
      acc = new BucketAccumulator(
        key,
        spec.surface,
        spec.storeyId,
        elementScoped ? spec.elementId : null,
        spec.glass,
        spec.overrideColor,
        spec.pickRoomByPoint,
      );
      buckets.set(key, acc);
    }

    const target = spec.pickRoomByPoint ? null : spec.pick;
    if (spec.cuts.length === 0) {
      appendPrism(acc, spec.profile, target);
      continue;
    }

    wantedCuts = true;
    const cutMesh = cutter === null ? null : cutter.cut(spec.profile, spec.cuts);
    if (cutMesh === null) {
      if (cutter === null) missedCuts = true;
      appendPrism(acc, spec.profile, target);
    } else {
      appendCutMesh(acc, cutMesh.positionsMm, target);
    }
  }

  return {
    key: groupKey,
    storeyId,
    buckets: [...buckets.values()].filter((a) => a.positions.length > 0).map((a) => a.toBucket()),
    holesApplied: !wantedCuts || !missedCuts,
  };
}
