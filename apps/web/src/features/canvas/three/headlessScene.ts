/**
 * headlessScene.ts — the building as a plain three `Group`, built without
 * React or a renderer, with EXACTLY the names and structure `ThreeDScene`
 * and `FacadeLayer` give the live scene:
 *
 *     three-d:<group key>   one Mesh per merged bucket   (the building)
 *     garh-facade           one Mesh per kit component   (the facade)
 *
 * WHY IT EXISTS: `gltfExport.ts` finds its roots by those names in the LIVE
 * scene, and a test that proves the GLB round-trips needs a scene to export
 * without a GPU. Building it here from the same `buildGroup` buckets, the
 * same `boxesForComponent` boxes, the same material factories and the same
 * NAMER (`meshNames.ts`) means the headless object is the on-screen object
 * minus React. (It is deliberately NOT a second renderer: nothing here draws.)
 *
 * WHAT IT CANNOT PROVE, AND WHO DOES. This file is a substitute, and a test
 * that exercises a substitute proves the substitute (CLAUDE.md bug 6). It
 * was one: it named its meshes while the live components did not, so the
 * export suite was green while every real download arrived as `mesh_0 …
 * mesh_N` — no renderer artist could select the external walls. Both live
 * components now call `meshNames.ts`, and the claim is held where it can go
 * red: `e2e/tests/three-d.spec.ts` downloads the GLB from the REAL scene and
 * reads those names out of the bytes. Keep it that way — if this file grows
 * a detail the live scene lacks, the suite is lying again.
 *
 * The facade read (`house.facade`) is confined to this file and the facade
 * module; `solids.ts` / `geometryBuild.ts` still never see it (§8).
 */

import { BufferAttribute, BufferGeometry, Group, Mesh } from 'three';

import type { HouseModel } from '@garh/model';

import { boxesForComponent } from '../facade/componentBoxes';
import { buildBoxTriangles } from '../facade/geometry3d';
import { createFacadeMaterial, FACADE_MESH_SHADOW } from '../facade/material3d';
import type { PrismCutter } from './booleans';
import { buildGroup, type BuiltBucket } from './geometryBuild';
import { FACADE_GROUP_NAME } from './gltfExport';
import { colorForScope, elementScopedAssignmentIds, getSolidMaterial } from './materials3d';
import { bucketMeshName, facadeMeshName } from './meshNames';
import { groupKeysOf } from './solids';

function bucketGeometry(bucket: BuiltBucket): BufferGeometry {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(bucket.positions, 3));
  geometry.setAttribute('normal', new BufferAttribute(bucket.normals, 3));
  // The box-mapped UVs travel with the GLB, so a renderer artist who swaps
  // our material for their own gets textures at the right physical scale.
  geometry.setAttribute('uv', new BufferAttribute(bucket.uvs, 2));
  geometry.computeBoundingSphere();
  return geometry;
}

export interface HeadlessBuildOptions {
  /** The boolean engine's cutter, or null for the honest fallback. */
  readonly cutter?: PrismCutter | null | undefined;
  /** materialId → hex, as the page passes to `ThreeDScene`. */
  readonly materialColors?: Readonly<Record<string, string>> | undefined;
  /** Include the facade kit meshes (default true). */
  readonly facade?: boolean | undefined;
}

/**
 * The building (and facade) as a `Group` named like the live scene. Caller
 * owns disposal of the geometries; materials are the shared singletons
 * (`disposeSolidMaterials`) plus one facade material per call.
 */
export function buildHouseObject(house: HouseModel, options: HeadlessBuildOptions = {}): Group {
  const cutter = options.cutter ?? null;
  const elementScoped = elementScopedAssignmentIds(house.materials);
  const root = new Group();
  root.name = 'three-d';

  for (const key of groupKeysOf(house)) {
    const build = buildGroup(house, key, cutter, elementScoped);
    const group = new Group();
    group.name = `three-d:${key}`;
    for (const bucket of build.buckets) {
      if (bucket.positions.length === 0) continue;
      const color = colorForScope(
        house.materials,
        { surface: bucket.surface, storeyId: bucket.storeyId, elementId: bucket.elementId },
        options.materialColors,
        bucket.overrideColor,
      );
      const mesh = new Mesh(bucketGeometry(bucket), getSolidMaterial(color, bucket.glass));
      mesh.name = bucketMeshName(bucket);
      mesh.castShadow = !bucket.glass;
      mesh.receiveShadow = true;
      group.add(mesh);
    }
    root.add(group);
  }

  if (options.facade !== false && house.facade.components.length > 0) {
    const facade = new Group();
    facade.name = FACADE_GROUP_NAME;
    const material = createFacadeMaterial();
    for (const component of house.facade.components) {
      const boxes = boxesForComponent(house, component);
      if (boxes.length === 0) continue;
      const data = buildBoxTriangles(boxes);
      const geometry = new BufferGeometry();
      geometry.setAttribute('position', new BufferAttribute(data.positions, 3));
      geometry.setAttribute('color', new BufferAttribute(data.colors, 3));
      geometry.setAttribute('normal', new BufferAttribute(data.normals, 3));
      geometry.computeBoundingSphere();
      const mesh = new Mesh(geometry, material);
      mesh.name = facadeMeshName(component);
      mesh.castShadow = FACADE_MESH_SHADOW.castShadow;
      mesh.receiveShadow = FACADE_MESH_SHADOW.receiveShadow;
      facade.add(mesh);
    }
    root.add(facade);
  }

  return root;
}
