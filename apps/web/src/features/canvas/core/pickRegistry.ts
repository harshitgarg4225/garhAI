/**
 * pickRegistry.ts — the map from "a thing the raycaster hit" to "an element in
 * the model".
 *
 * THE RULE THIS FILE EXISTS TO ENFORCE (§12): **no module attaches
 * react-three-fiber pointer handlers to its meshes.** Not `onClick`, not
 * `onPointerOver`. A mesh that wants to be clickable registers here, and every
 * pick in the product — 2D and 3D, click and hover and marquee — goes through
 * `hitTest.pickAt`.
 *
 * Two reasons, and the second is the one §12 actually cares about:
 *
 *  1. Cost. R3F raycasts its interaction list on every `pointermove`, and it
 *     re-renders React for state set from those handlers. A plan with 400 walls
 *     and 300 furniture items would raycast 700 objects per mouse move through
 *     a code path we do not control. Here, hover is rAF-coalesced, the object
 *     list is a cached array, and a hover that lands on the same element as
 *     last frame writes nothing.
 *
 *  2. One picker. If 2D picks through R3F events and 3D picks through a
 *     raycaster, the priority rules ("an opening beats its host wall") exist
 *     twice and drift immediately. Phase 5 inherits this registry as-is: a 3D
 *     wall mesh registers exactly like a 2D one, and the same `PickHit` comes
 *     back out.
 *
 * INSTANCING. Furniture and openings are drawn as `InstancedMesh` for the §14
 * frame budget, so "which object" is not enough — the resolver is handed the
 * whole `Intersection` and reads `instanceId`. That is why registration takes a
 * resolver function and not just a target: an instanced mesh is one object and
 * three hundred elements.
 */

import type { Intersection, Object3D } from 'three';

import type { PickKind } from './constants';

/** What a pick resolves to: an element of the model document. */
export interface PickTarget {
  readonly kind: PickKind;
  /** The `{type}_{ulid}` element id. Never an object uuid. */
  readonly id: string;
  /** Storey the element belongs to, or null for storey-agnostic things. */
  readonly storeyId: string | null;
}

/**
 * Turns one raycast intersection into a target, or `null` for "this particular
 * hit is not pickable" (an instance slot that is currently unused, a wall on a
 * hidden storey).
 */
export type PickResolver = (intersection: Intersection) => PickTarget | null;

/** Per-instance id lookup for an `InstancedMesh`. */
export type InstanceIdLookup = readonly string[] | ((instanceId: number) => string | null);

/**
 * The registry. One instance per `CanvasRoot`, handed out through context.
 * Deliberately a plain class rather than a store: it changes on mount/unmount
 * of geometry, never during interaction, and nothing should re-render because
 * a mesh registered itself.
 */
export class PickRegistry {
  private readonly resolvers = new Map<Object3D, PickResolver>();

  /** Cached `objects()` result; rebuilt only when membership changes. */
  private cache: Object3D[] = [];

  private dirty = false;

  /** How many objects are currently pickable. */
  get size(): number {
    return this.resolvers.size;
  }

  /**
   * Make `object` pickable. Pass a fixed target for a one-element mesh, or a
   * resolver for anything instanced or conditional.
   *
   * Returns the unregister function — bind it straight to a `useEffect`
   * cleanup so a mesh can never outlive its registration.
   */
  register(object: Object3D, target: PickTarget | PickResolver): () => void {
    const resolver: PickResolver = typeof target === 'function' ? target : () => target;
    this.resolvers.set(object, resolver);
    this.dirty = true;
    return () => {
      this.unregister(object);
    };
  }

  /**
   * Make an `InstancedMesh` pickable, mapping `instanceId` → element id.
   *
   * The array form is read live, so a module may mutate its id array in place
   * as instances are recycled without re-registering — which is the whole point
   * of instancing: the mesh is stable and the contents churn.
   */
  registerInstanced(
    object: Object3D,
    kind: PickKind,
    ids: InstanceIdLookup,
    storeyId: string | null = null,
  ): () => void {
    const lookup: (i: number) => string | null =
      typeof ids === 'function' ? ids : (i) => ids[i] ?? null;
    return this.register(object, (hit) => {
      const instanceId = hit.instanceId;
      if (instanceId === undefined) return null;
      const id = lookup(instanceId);
      return id === null ? null : { kind, id, storeyId };
    });
  }

  unregister(object: Object3D): void {
    if (this.resolvers.delete(object)) this.dirty = true;
  }

  clear(): void {
    if (this.resolvers.size === 0) return;
    this.resolvers.clear();
    this.dirty = true;
  }

  /** Resolve one intersection. `null` when the object is not registered. */
  resolve(intersection: Intersection): PickTarget | null {
    const resolver = this.resolvers.get(intersection.object);
    return resolver === undefined ? null : resolver(intersection);
  }

  /**
   * The array to hand `Raycaster.intersectObjects`.
   *
   * PERF: the same array instance is returned until membership changes, so the
   * hot path allocates nothing. Callers must not mutate it.
   */
  objects(): Object3D[] {
    if (this.dirty) {
      this.cache = Array.from(this.resolvers.keys());
      this.dirty = false;
    }
    return this.cache;
  }
}

/**
 * True when `object` and every ancestor is visible.
 *
 * `Raycaster` does **not** check `.visible` on the objects it is handed
 * directly (only while descending recursively), and we hand it a flat list —
 * so without this, hiding a storey would hide it on screen and leave it
 * clickable. That is a real bug class: you delete a wall you cannot see.
 */
export function isEffectivelyVisible(object: Object3D): boolean {
  let node: Object3D | null = object;
  while (node !== null) {
    if (!node.visible) return false;
    node = node.parent;
  }
  return true;
}
