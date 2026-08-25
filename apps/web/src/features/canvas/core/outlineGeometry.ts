/**
 * outlineGeometry.ts — plan millimetres → renderable buffers.
 *
 * Split out of `outline.tsx` so the conversion is pure, testable without a
 * React renderer, and reusable by any module that needs a footprint on screen
 * (wall bodies, room fills, the marquee, Phase 5's floor slabs) rather than
 * only by the selection primitives.
 *
 * Everything here takes integer-mm plan geometry and produces world-space
 * floats. That direction is the only one that exists: nothing in this file
 * converts back, because a rendered buffer is not a source of truth.
 */

import { triangulate, type Bbox, type Polygon, type Pt } from '@garh/model';
import { BufferAttribute, BufferGeometry } from 'three';

import { WORLD_UNITS_PER_MM } from './constants';

/**
 * Plan points → world triples, ready for a line renderer.
 *
 * PERF: allocates one array of arrays. Memoise on the caller's side — this is
 * fine per selection change and wasteful per frame.
 */
export function pointsMmToWorld(
  points: readonly Pt[],
  elevationMm = 0,
  closed = false,
): [number, number, number][] {
  const y = elevationMm * WORLD_UNITS_PER_MM;
  const out: [number, number, number][] = points.map((p) => [
    p.x * WORLD_UNITS_PER_MM,
    y,
    // North is −Z. The same flip as `coords.ts`, and the reason a plan polygon
    // comes out clockwise when viewed from above — which is why every fill
    // material in `materials.ts` is `DoubleSide`.
    -p.y * WORLD_UNITS_PER_MM,
  ]);
  const first = out[0];
  if (closed && first !== undefined && out.length > 2) out.push([first[0], first[1], first[2]]);
  return out;
}

/**
 * Triangulated fill geometry for a plan polygon, in world space.
 *
 * Uses `@garh/model`'s exact integer ear-clipping rather than three's
 * `ShapeGeometry`: that is the same triangulation the area figures and the
 * sheet engine use, and a second floating-point notion of "the inside of this
 * room" would disagree at exactly the concave corners that matter.
 *
 * The caller owns the returned geometry and must `dispose()` it.
 */
export function polygonFillGeometry(polygon: Polygon, elevationMm = 0): BufferGeometry {
  const triangles = triangulate(polygon);
  const positions = new Float32Array(triangles.length * 9);
  const y = elevationMm * WORLD_UNITS_PER_MM;
  let i = 0;
  for (const triangle of triangles) {
    for (const p of triangle) {
      positions[i] = p.x * WORLD_UNITS_PER_MM;
      positions[i + 1] = y;
      positions[i + 2] = -p.y * WORLD_UNITS_PER_MM;
      i += 3;
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(positions, 3));
  geometry.computeBoundingSphere();
  return geometry;
}

/** The four corners of a bbox as a CCW plan ring. */
export function bboxRingMm(box: Bbox): Pt[] {
  return [
    { x: box.minX, y: box.minY },
    { x: box.maxX, y: box.minY },
    { x: box.maxX, y: box.maxY },
    { x: box.minX, y: box.maxY },
  ];
}
