/**
 * persist.ts — layer state in `localStorage`, per project and per user.
 *
 * WHY LOCAL AND NOT THE SERVER. Which layers you have switched off is not a
 * property of the design — it changes no geometry, appears in no op, and must
 * never reach the op log or another collaborator's screen. Two architects on
 * the same project routinely want different layers off; syncing that would be
 * a bug, not a feature. It belongs in the browser.
 *
 * WHY PER PROJECT AND PER USER. The key carries both. A shared machine in a
 * studio is normal, and "I hid dimensions on the Sharma house" must not follow
 * you into the next project or onto your colleague's session.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EVERY ACCESS IS WRAPPED, AND THAT IS NOT DEFENSIVE PROGRAMMING THEATRE
 * ════════════════════════════════════════════════════════════════════════════
 * `localStorage` does not merely return null when unavailable. Safari in
 * private browsing has historically thrown `QuotaExceededError` on every
 * `setItem`; Chrome throws `SecurityError` on the mere PROPERTY ACCESS when
 * third-party cookies are blocked and the app is framed — which is exactly how
 * a share link gets embedded. Touching `window.localStorage` at all is the
 * throwing operation, so the try/catch has to be around that, not just around
 * the call.
 *
 * The contract this file offers the store is therefore total: reading always
 * answers a value (possibly "nothing stored"), writing never throws, and a
 * browser with storage switched off gets a perfectly working layer panel that
 * forgets between sessions. Losing the memory is acceptable. Failing to render
 * the plan because the memory was unreadable is not.
 */

import { DRAWING_LAYER_NAMES, isDrawingLayerName, type DrawingLayerName } from './layerSpecs';
import type { LayerFlags } from './mapping';

/**
 * Bumped when the stored shape changes. An old payload under a superseded
 * version is simply not read — a silent migration of a preferences blob is not
 * worth the code, and the cost of forgetting is one re-toggle.
 */
const STORAGE_VERSION = 1;

const KEY_PREFIX = `garh:layers:v${STORAGE_VERSION}`;

/** Who and what the state belongs to. */
export interface LayerScope {
  readonly userId: string;
  readonly projectId: string;
}

/** The persisted payload. Isolate state is deliberately not in it — see below. */
export interface PersistedLayerState {
  readonly visible: LayerFlags;
  readonly locked: LayerFlags;
}

/**
 * All nine on / all nine off.
 *
 * Returns a MUTABLE record rather than `LayerFlags`, and a fresh one on every
 * call, because both callers build on top of it (isolate turns one back on;
 * `coerceFlags` overwrites what storage supplied). It is assignable to
 * `LayerFlags` wherever the readonly view is wanted.
 */
export function allLayers(value: boolean): Record<DrawingLayerName, boolean> {
  const out = {} as Record<DrawingLayerName, boolean>;
  for (const name of DRAWING_LAYER_NAMES) out[name] = value;
  return out;
}

/**
 * The state a project opens in: everything visible, nothing locked.
 *
 * Visible-by-default matters. A drafter turns layers off when a drawing gets
 * busy; a new user who is shown an incomplete plan because of a default has no
 * way to know what is missing.
 */
export function defaultLayerState(): PersistedLayerState {
  return { visible: allLayers(true), locked: allLayers(false) };
}

export function storageKey(scope: LayerScope): string {
  return `${KEY_PREFIX}:${scope.userId}:${scope.projectId}`;
}

/**
 * The storage object, or null if reaching for it throws or it is absent.
 *
 * Separate from the read/write helpers because the property access is itself
 * the dangerous part (see the header) and both helpers need it guarded.
 */
function safeStorage(): Storage | null {
  try {
    // `globalThis.localStorage` rather than `window.` so this is also safe in
    // the Node half of the test run and in any future SSR pass.
    const storage = globalThis.localStorage;
    return storage ?? null;
  } catch {
    return null;
  }
}

/**
 * Coerce whatever was stored into a complete, valid state.
 *
 * Unknown layer names are dropped and missing ones fall back to the default,
 * so a payload written by an older build (eight layers) or a newer one (ten)
 * still yields a usable state rather than a `Record` with holes in it. This is
 * the same discipline the model applies at its own boundaries: never trust a
 * shape you did not construct in this process.
 */
function coerceFlags(raw: unknown, fallback: boolean): LayerFlags {
  const out = allLayers(fallback);
  if (typeof raw !== 'object' || raw === null) return out;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (isDrawingLayerName(key) && typeof value === 'boolean') out[key] = value;
  }
  return out;
}

/**
 * Read the stored state for a scope, or null when there is nothing usable.
 *
 * Null and "stored garbage" are the same answer on purpose: the caller's only
 * sensible response to either is the default state, and distinguishing them
 * would invite a branch that renders differently for a corrupt payload.
 */
export function readLayerState(scope: LayerScope): PersistedLayerState | null {
  const storage = safeStorage();
  if (storage === null) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(storageKey(scope));
  } catch {
    return null;
  }
  if (raw === null || raw === '') return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return null;
    const record = parsed as { visible?: unknown; locked?: unknown };
    return {
      visible: coerceFlags(record.visible, true),
      locked: coerceFlags(record.locked, false),
    };
  } catch {
    return null;
  }
}

/**
 * Write the state for a scope. Answers whether it actually landed, which the
 * store does not currently use and a future "your preferences are not being
 * saved" notice would.
 *
 * Note what is NOT written: isolate. Isolate is a temporary mode with a
 * remembered exit, and a session that ends mid-isolate must not reopen showing
 * one layer with no memory of why. Its snapshot dies with the tab; the
 * underlying visibility it would restore is what gets persisted.
 */
export function writeLayerState(scope: LayerScope, state: PersistedLayerState): boolean {
  const storage = safeStorage();
  if (storage === null) return false;
  try {
    storage.setItem(storageKey(scope), JSON.stringify(state));
    return true;
  } catch {
    // Quota, private mode, storage disabled. The panel keeps working; the
    // preference simply will not survive the tab.
    return false;
  }
}

/** Forget a scope's state. Used by "reset layers" in the panel. */
export function clearLayerState(scope: LayerScope): boolean {
  const storage = safeStorage();
  if (storage === null) return false;
  try {
    storage.removeItem(storageKey(scope));
    return true;
  } catch {
    return false;
  }
}
