/**
 * pickGate.ts — how a locked layer stops being clickable.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ONE PLACE, NOT NINE
 * ════════════════════════════════════════════════════════════════════════════
 * `hitTest.pickAt` is the only picker in the product — every click, hover,
 * marquee, snap query and tool commit in 2D and 3D goes through it — and for
 * each intersection it calls `registry.resolve` exactly once. That single call
 * is the choke point where "this element is on a locked layer" can be said
 * once and be true everywhere: the select tool, the wall tool's snapping, the
 * inspector's hover, the copilot's focus, and anything Phase 6 adds later.
 *
 * The alternative — teaching each tool about locks — is the shape of the bug
 * this repository has already shipped: the furniture layer that documented
 * itself as integrated and never called the registry. Nine tools each
 * remembering a rule is nine chances for the tenth to forget, with no
 * compile-time signal when it does.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY THIS DECORATES AN INSTANCE INSTEAD OF ADDING A FIELD TO PickRegistry
 * ════════════════════════════════════════════════════════════════════════════
 * `PickRegistry` has no filter seam, and it belongs to `features/canvas/core` —
 * a module this feature may read but does not own. So the gate wraps `resolve`
 * on the ONE registry instance the canvas core hands out, which is
 * behaviourally identical to a `setFilter` method and needs no change to the
 * core to ship.
 *
 * It is written to be safe about that:
 *   · idempotent — installing twice replaces the first gate, never stacks;
 *   · reversible — uninstall deletes the own property and the prototype method
 *     takes over again, so a registry that has been gated and ungated is
 *     indistinguishable from one that never was;
 *   · inert by construction — it can only turn a resolved target into `null`,
 *     which is the value the registry already returns for "this hit is not
 *     pickable". It cannot invent a target, change a kind, or reorder anything.
 *
 * If `PickRegistry` ever grows a first-class filter, this file becomes three
 * lines and the rest of the feature does not move.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE STATE IS READ PER RESOLVE, ON PURPOSE
 * ════════════════════════════════════════════════════════════════════════════
 * `read()` is called on each resolve rather than captured at install time, so
 * locking a layer takes effect on the very next click with no re-install, no
 * registry churn and no stale closure. It must therefore be cheap: the caller
 * memoises the blocked set on the document and the layer state, and this file
 * does two `Set.has` calls.
 */

import type { PickRegistry, PickTarget } from '../canvas/core/pickRegistry';
import type { LayerPickBlock } from './mapping';

/** Marker for the uninstall function of a gate already on this registry. */
const GATE = Symbol.for('garh.layers.pickGate');

type Gated = PickRegistry & { [GATE]?: () => void };

/**
 * Make the registry refuse anything on a locked or hidden layer.
 *
 * Returns the uninstall function — bind it straight to a `useEffect` cleanup,
 * the same shape `PickRegistry.register` uses, so a gate can never outlive the
 * canvas it was installed on.
 */
export function installLayerPickGate(
  registry: PickRegistry,
  read: () => LayerPickBlock,
): () => void {
  const target = registry as Gated;

  // Replace rather than stack. Two gates would each call the other's `inner`,
  // and uninstalling the first would restore a resolve that still consults the
  // second's `read` — a leak that only shows up as picks that stay blocked
  // after the panel is gone.
  target[GATE]?.();

  const inner = registry.resolve.bind(registry);

  const gated = (intersection: Parameters<PickRegistry['resolve']>[0]): PickTarget | null => {
    const resolved = inner(intersection);
    if (resolved === null) return null;
    const block = read();
    if (block.ids.has(resolved.id)) return null;
    if (block.kinds.has(resolved.kind)) return null;
    return resolved;
  };

  registry.resolve = gated;

  const uninstall = (): void => {
    // Another gate may have replaced this one in the meantime; it owns the
    // property now and removing it would strip a live gate.
    if (registry.resolve !== gated) return;
    // Deleting the own property un-shadows `PickRegistry.prototype.resolve`,
    // which is the original implementation — no copy of it is kept anywhere.
    delete (registry as unknown as Record<string, unknown>).resolve;
    delete target[GATE];
  };

  target[GATE] = uninstall;
  return uninstall;
}

/** True when a gate is currently installed. Test support and diagnostics. */
export function hasLayerPickGate(registry: PickRegistry): boolean {
  return (registry as Gated)[GATE] !== undefined;
}
