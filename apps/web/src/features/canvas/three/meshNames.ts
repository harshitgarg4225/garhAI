/**
 * meshNames.ts — what each mesh in the 3D scene is CALLED.
 *
 * WHY THIS MODULE EXISTS (found by an adversarial review, 2026-09-20).
 * `gltfExport.ts` finds and labels geometry by `Object3D.name`, and the GLB
 * an architect downloads carries those names into Lumion, D5 or Blender —
 * they are how a renderer artist selects "the external walls". But
 * react-three-fiber never sets `name` on its own, and neither `BucketMesh`
 * nor `FacadeComponentMesh` passed one, so every object in a real export was
 * `mesh_0 … mesh_N`. The export TEST missed it because it exported a
 * headless fixture that named its own meshes — CLAUDE.md bug 6 exactly: a
 * test that exercises a substitute proves the substitute.
 *
 * So the names live HERE, in one dependency-free module, and the live
 * components, the headless builder and the exporter all read them from it.
 * A mesh producer that forgets to call these still ships nameless meshes —
 * there is no compile-time signal for that — which is why the e2e spec reads
 * the DOWNLOADED BYTES for these strings (`three-d.spec.ts`), on the real
 * scene graph, where a regression can actually go red.
 */

/** What the exporter needs to know about a merged building bucket. */
export interface NamedBucket {
  readonly surface: string;
  readonly elementId: string | null;
  readonly glass: boolean;
}

/** What it needs about a facade kit component. */
export interface NamedFacadeComponent {
  readonly id: string;
  readonly kind: string;
}

/**
 * The name of one merged building mesh: its surface group, plus the element
 * when the mesh is a single element with its own material, plus a `_glass`
 * marker so glazing is separable from the leaf it sits beside.
 *
 * `glTF` node names lose `[ ] . : /` on the way back through `GLTFLoader`
 * (three's `PropertyBinding.sanitizeNodeName`), so nothing here uses them.
 */
export function bucketMeshName(bucket: NamedBucket): string {
  const scope = bucket.elementId === null ? '' : `_${bucket.elementId}`;
  return `${bucket.surface}${scope}${bucket.glass ? '_glass' : ''}`;
}

/** The name of one facade component's mesh — kind first, so a list sorts usefully. */
export function facadeMeshName(component: NamedFacadeComponent): string {
  return `facade_${component.kind}_${component.id}`;
}
