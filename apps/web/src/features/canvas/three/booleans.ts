/**
 * booleans.ts — THE Manifold boundary. The only file in the workspace that
 * imports `manifold-3d`, and it imports it lazily, so the app ships and loads
 * with zero WASM until the 3D view actually mounts.
 *
 * HONEST DEGRADATION, NOT A CRASH: when the WASM cannot load (package not
 * installed, network-blocked chunk, WASM disabled), the engine reports
 * `{ state: 'unavailable', reason }` and every caller falls back to plain
 * prisms — walls render WITHOUT their opening holes, opening panels still
 * render and stay pickable, and `ThreeDScene` surfaces the state through
 * `onEngineStatus` so the page can say so in words (§15: honest, teaching UI
 * — not a silently wrong drawing).
 *
 * Per-solid degradation too: a boolean that throws (degenerate tool, WASM
 * out-of-memory) returns null for THAT solid; the caller draws the uncut
 * prism and every other wall keeps its holes.
 *
 * COORDINATES: this module works in model space — float mm, +X east, +Y
 * north, +Z elevation. `geometryBuild.ts` converts the result to world units;
 * keeping mm here means the cut boxes can be compared against op payload
 * numbers when debugging.
 *
 * MEMORY: Manifold objects are WASM-heap handles, not garbage-collected.
 * Every constructed Manifold in `cut()` is `.delete()`d before return, on
 * every path, or the tab leaks the heap one boolean at a time.
 */

import type { Manifold as ManifoldSolid, ManifoldToplevel, Vec2 } from 'manifold-3d';

import type { PrismProfileF } from './extrusion';

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

export type BooleanEngineStatus =
  | { readonly state: 'idle' }
  | { readonly state: 'loading' }
  | { readonly state: 'ready' }
  | { readonly state: 'unavailable'; readonly reason: string };

/** A boolean-cut result: non-indexed triangles, mm model space (x, y, z-up). */
export interface CutMeshMm {
  /** xyz interleaved, three vertices per triangle. */
  readonly positionsMm: Float32Array;
}

export interface PrismCutter {
  /**
   * Extrude `profile` and subtract every `cut`. Returns null when the boolean
   * fails for this particular solid — the caller must fall back to the uncut
   * prism (and only for this solid).
   */
  cut(profile: PrismProfileF, cuts: readonly PrismProfileF[]): CutMeshMm | null;
}

// ---------------------------------------------------------------------------
// Module state (one engine per tab — the WASM instance is a heavyweight)
// ---------------------------------------------------------------------------

let status: BooleanEngineStatus = { state: 'idle' };
let toplevel: ManifoldToplevel | null = null;
let loadPromise: Promise<BooleanEngineStatus> | null = null;
const listeners = new Set<(s: BooleanEngineStatus) => void>();

function setStatus(next: BooleanEngineStatus): void {
  status = next;
  for (const listener of listeners) listener(next);
}

export function booleanEngineStatus(): BooleanEngineStatus {
  return status;
}

/** Subscribe to status changes. Returns the unsubscribe function. */
export function subscribeBooleanEngine(listener: (s: BooleanEngineStatus) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Load the WASM once. Idempotent and re-entrant: concurrent callers share one
 * promise, and a settled engine resolves immediately. Never throws — failure
 * IS a result here (`unavailable`), because a missing optional capability is
 * not an exception.
 */
export function ensureBooleanEngine(): Promise<BooleanEngineStatus> {
  if (loadPromise !== null) return loadPromise;
  loadPromise = (async (): Promise<BooleanEngineStatus> => {
    setStatus({ state: 'loading' });
    try {
      // The ONE dynamic import. Everything upstream of this line runs with no
      // WASM on the page; Vite splits `manifold-3d` into its own async chunk.
      const mod = await import('manifold-3d');
      const wasm = await mod.default();
      wasm.setup();
      toplevel = wasm;
      setStatus({ state: 'ready' });
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      setStatus({
        state: 'unavailable',
        reason: `manifold-3d failed to load (${reason}) — walls render without opening holes`,
      });
    }
    return status;
  })();
  return loadPromise;
}

/** The cutter, or null until (unless) the engine is ready. */
export function getPrismCutter(): PrismCutter | null {
  const wasm = toplevel;
  if (wasm === null || status.state !== 'ready') return null;
  return {
    cut(profile, cuts) {
      return cutPrism(wasm, profile, cuts);
    },
  };
}

/** Test hook: forget the engine so a spec can exercise the load path fresh. */
export function __resetBooleanEngineForTests(): void {
  status = { state: 'idle' };
  toplevel = null;
  loadPromise = null;
  listeners.clear();
}

// ---------------------------------------------------------------------------
// The boolean itself
// ---------------------------------------------------------------------------

function toVec2Ring(polygon: PrismProfileF['polygon']): Vec2[] {
  return polygon.map((p): Vec2 => [p.x, p.y]);
}

function cutPrism(
  wasm: ManifoldToplevel,
  profile: PrismProfileF,
  cuts: readonly PrismProfileF[],
): CutMeshMm | null {
  const { Manifold } = wasm;
  const heightMm = profile.topMm - profile.baseMm;
  if (heightMm <= 0 || profile.polygon.length < 3) return null;

  // `current` is the never-null working solid; `solid` mirrors it only so the
  // finally can free whatever exists when an exception unwinds. Keeping the
  // loop on `current` also breaks the type-inference cycle the compiler sees
  // when `solid = next` feeds `next = solid.subtract(...)`.
  let solid: ManifoldSolid | null = null;
  try {
    let current = Manifold.extrude([toVec2Ring(profile.polygon)], heightMm).translate([
      0,
      0,
      profile.baseMm,
    ]);
    solid = current;
    for (const cut of cuts) {
      const cutHeight = cut.topMm - cut.baseMm;
      if (cutHeight <= 0 || cut.polygon.length < 3) continue;
      const tool = Manifold.extrude([toVec2Ring(cut.polygon)], cutHeight).translate([
        0,
        0,
        cut.baseMm,
      ]);
      const next = current.subtract(tool);
      tool.delete();
      current.delete();
      current = next;
      solid = current;
    }

    const mesh = current.getMesh();
    const stride = mesh.numProp;
    const triCount = mesh.triVerts.length / 3;
    const positionsMm = new Float32Array(triCount * 9);
    let v = 0;
    for (const vi of mesh.triVerts) {
      const base = vi * stride;
      positionsMm[v] = mesh.vertProperties[base] ?? 0;
      positionsMm[v + 1] = mesh.vertProperties[base + 1] ?? 0;
      positionsMm[v + 2] = mesh.vertProperties[base + 2] ?? 0;
      v += 3;
    }
    return { positionsMm };
  } catch {
    // Per-solid fallback: this wall draws uncut; the rest of the storey is
    // unaffected. Deliberately not a status change — the engine still works.
    return null;
  } finally {
    solid?.delete();
  }
}
