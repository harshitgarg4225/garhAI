/**
 * gltfExport.ts — the building you are looking at, as a GLB, from the ONE
 * live scene. The client-side half of J06's 3D export (the server half is
 * `POST /projects/:id/export kind=gltf`, drawn by the drawings worker from
 * the model — see `ExportPanel3d`).
 *
 * WHAT GOES IN: the extruded building — every rebuild group `ThreeDScene`
 * mounts under `three-d:<group>` — and the facade kit meshes under
 * `garh-facade`. Both are found BY NAME in the scene graph, so the export is
 * exactly the geometry on screen: cut walls if the engine cut them, the
 * proud fallback panels if it did not, the storey filter's visibility
 * honoured (`onlyVisible`). Lights, the ground mat, the grid, selection
 * rings and every DOM overlay stay out — a renderer artist wants the house,
 * not the viewport.
 *
 * UNITS + AXES come free: the scene is already metres, Y-up (`coords.ts`),
 * which is glTF's own convention — the same mapping the worker's exporter
 * states as its first two decisions. Materials export as they render:
 * `MeshStandardMaterial` colour/roughness/metalness, vertex colours for the
 * facade, `doubleSided` where the building's materials are.
 *
 * Pure over three: no React, no DOM beyond what `GLTFExporter` itself needs
 * (`Blob` + `FileReader`), so `gltfExport.test.ts` round-trips a real GLB
 * through `GLTFLoader` in node.
 */

import { Group, type Mesh, type Object3D } from 'three';
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js';

/** Name prefix of the building's rebuild groups (`ThreeDScene`). */
export const BUILDING_GROUP_PREFIX = 'three-d:';

/** Name of the facade layer's group (`FacadeLayer`). */
export const FACADE_GROUP_NAME = 'garh-facade';

/** Is this object one of the groups the export takes? */
export function isExportRoot(object: Object3D): boolean {
  return object.name === FACADE_GROUP_NAME || object.name.startsWith(BUILDING_GROUP_PREFIX);
}

/** The export roots present under `scene`, in traversal order. */
export function exportRootsOf(scene: Object3D): Object3D[] {
  const out: Object3D[] = [];
  scene.traverse((object) => {
    if (isExportRoot(object)) out.push(object);
  });
  return out;
}

/** Type guard on three's own flag: `instanceof Mesh` narrows to `Mesh<any>`. */
function isMesh(object: Object3D): object is Mesh {
  return (object as Partial<Mesh>).isMesh === true;
}

/** Visible meshes with faces under `roots` — what the GLB will carry. */
export function countExportableMeshes(roots: readonly Object3D[]): number {
  let count = 0;
  for (const root of roots) {
    root.traverse((object) => {
      if (!isMesh(object)) return;
      if (!object.visible) return;
      const position = object.geometry.getAttribute('position');
      if (position === undefined || position.count === 0) return;
      count += 1;
    });
  }
  return count;
}

/** Walks up from `object`; false if any ancestor is hidden. */
function effectivelyVisible(object: Object3D): boolean {
  let node: Object3D | null = object;
  while (node !== null) {
    if (!node.visible) return false;
    node = node.parent;
  }
  return true;
}

/**
 * Export `roots` as a binary glTF. Returns the GLB bytes.
 *
 * The roots are CLONED under a fresh group (geometry and materials shared —
 * a clone is a handful of matrices), so the exporter never re-parents or
 * touches the live scene; hidden branches are pruned by the exporter's own
 * `onlyVisible`, with the roots' own effective visibility checked here
 * because a clone forgets its original ancestors.
 */
export function exportGlb(roots: readonly Object3D[]): Promise<ArrayBuffer> {
  const root = new Group();
  root.name = 'garh-building';
  for (const source of roots) {
    if (!effectivelyVisible(source)) continue;
    root.add(source.clone(true));
  }
  return new Promise((resolve, reject) => {
    new GLTFExporter().parse(
      root,
      (result) => {
        if (result instanceof ArrayBuffer) resolve(result);
        else reject(new Error('GLTFExporter returned JSON for a binary request'));
      },
      (error: unknown) => {
        reject(error instanceof Error ? error : new Error(String(error)));
      },
      { binary: true, onlyVisible: true },
    );
  });
}

/** `Studio Demo House` → `studio-demo-house.glb`. Never empty. */
export function glbFileName(projectName: string): string {
  const slug = projectName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `${slug === '' ? 'garh-model' : slug}.glb`;
}
