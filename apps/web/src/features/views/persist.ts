/**
 * persist.ts — saved views in `localStorage`, per project and per user.
 *
 * WHY LOCAL AND NOT THE SERVER. A named view changes no geometry, appears in no
 * op, and belongs in no undo entry. Two architects on the same project want
 * different views — one is detailing a kitchen, the other is checking the
 * street elevation — and syncing "my camera bookmarks" onto a colleague's
 * screen would be a bug wearing a feature's clothes. The same argument
 * `features/layers/persist.ts` makes for layer visibility, for the same reason.
 *
 * WHY THE KEY CARRIES BOTH THE USER AND THE PROJECT. Studio machines are
 * shared. "Kitchen detail" on the Sharma house must not follow you into the
 * next project, and must not appear in your colleague's list.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EVERY ACCESS IS WRAPPED, AND THE WRAP IS AROUND THE PROPERTY READ
 * ════════════════════════════════════════════════════════════════════════════
 * `localStorage` does not merely return null when it is unavailable. Safari in
 * private browsing has historically thrown `QuotaExceededError` on every
 * `setItem`, and Chrome throws `SecurityError` on the PROPERTY ACCESS itself
 * when third-party cookies are blocked and the app is framed — which is exactly
 * how a share link gets embedded. So `globalThis.localStorage` is inside the
 * try, not just the call on it.
 *
 * The contract is total: reading always answers (possibly "nothing stored"),
 * writing never throws, and a browser with storage switched off gets a views
 * panel that works perfectly and forgets between sessions. Losing the memory is
 * acceptable. Failing to draw the plan because the memory was unreadable is
 * not.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IS VALIDATED, AND WHY IT IS MORE THAN PARANOIA
 * ════════════════════════════════════════════════════════════════════════════
 * A stored camera is the one input to this feature that this process did not
 * construct. If a payload written by an older build (different zoom clamps), or
 * by a user with the devtools open, came back with a `mmPerPx` of 10 000, then
 * `ViewportController.setView2d` would clamp it on restore and the view would
 * land somewhere other than where the record says — the exactness promise,
 * broken silently, on exactly the path nobody tests by hand. So every payload
 * is coerced through `normaliseCamera` on the way in, and what the store holds
 * is always a camera the controller accepts unchanged.
 */

import { isStorableCamera, normaliseCamera } from './camera';
import type { NamedView, SavedCamera, ViewsScope } from './types';

/**
 * Bumped when the stored shape changes. An old payload under a superseded
 * version is simply not read: silently migrating a preferences blob is not
 * worth the code, and the cost of forgetting is re-saving a few views.
 */
const STORAGE_VERSION = 1;

const KEY_PREFIX = `garh:views:v${STORAGE_VERSION}`;

/**
 * How many views one project may keep.
 *
 * A cap exists because `localStorage` has a quota (5 MB typically, shared
 * across the whole origin) and a list that grows without bound eventually
 * starts throwing on write — at which point the panel keeps accepting saves and
 * silently keeps none of them. Forty named views is far past what anyone
 * curates; refusing the forty-first with a message beats losing all of them.
 */
export const MAX_VIEWS = 40;

/** Longest a view name may be. Anything longer is truncated, not rejected. */
export const MAX_NAME_LENGTH = 60;

export function storageKey(scope: ViewsScope): string {
  return `${KEY_PREFIX}:${scope.userId}:${scope.projectId}`;
}

/**
 * The storage object, or null if reaching for it throws or it is absent.
 * Separate from the read/write helpers because the property access is itself
 * the dangerous part (see the header) and both helpers need it guarded.
 */
function safeStorage(): Storage | null {
  try {
    // `globalThis.localStorage`, not `window.` — also correct in the Node half
    // of the test run and in any future SSR pass.
    const storage = globalThis.localStorage;
    return storage ?? null;
  } catch {
    return null;
  }
}

/** Trim, collapse whitespace, and cap. Empty is the caller's problem, not ours. */
export function cleanViewName(name: string): string {
  return name.replace(/\s+/g, ' ').trim().slice(0, MAX_NAME_LENGTH);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Parse one stored camera, or null if it is not one.
 *
 * Every field is read individually and checked for finiteness. A missing angle
 * must not become `undefined` inside an orbit: that produces a camera matrix of
 * NaNs, a scene that renders nothing, and no error anywhere — the same shape as
 * the "83 rules went inert" bug, where a value outside its own domain was
 * accepted and everything downstream quietly stopped meaning anything.
 */
export function parseCamera(raw: unknown): SavedCamera | null {
  if (!isRecord(raw)) return null;

  if (raw.mode === '2d') {
    const centre = raw.centreMm;
    if (!isRecord(centre)) return null;
    const x = num(centre.x);
    const y = num(centre.y);
    const mmPerPx = num(raw.mmPerPx);
    if (x === null || y === null || mmPerPx === null) return null;
    return normaliseCamera({ mode: '2d', centreMm: { x, y }, mmPerPx });
  }

  if (raw.mode === '3d') {
    const target = raw.targetMm;
    if (!isRecord(target)) return null;
    const x = num(target.x);
    const y = num(target.y);
    const z = num(target.z);
    const distanceMm = num(raw.distanceMm);
    const azimuthDeg = num(raw.azimuthDeg);
    const polarDeg = num(raw.polarDeg);
    if (x === null || y === null || z === null) return null;
    if (distanceMm === null || azimuthDeg === null || polarDeg === null) return null;
    return normaliseCamera({
      mode: '3d',
      targetMm: { x, y, z },
      distanceMm,
      azimuthDeg,
      polarDeg,
    });
  }

  return null;
}

/**
 * Parse one stored view. A view with an unusable camera is dropped rather than
 * repaired: a bookmark to nowhere is worse than a missing bookmark, because the
 * user clicks it and blames the app.
 */
function parseView(raw: unknown, seen: Set<string>): NamedView | null {
  if (!isRecord(raw)) return null;
  const id = typeof raw.id === 'string' ? raw.id.trim() : '';
  if (id === '' || seen.has(id)) return null;
  const camera = parseCamera(raw.camera);
  if (camera === null) return null;
  const name = cleanViewName(typeof raw.name === 'string' ? raw.name : '');
  const createdAt = num(raw.createdAt);
  seen.add(id);
  return {
    id,
    name: name === '' ? 'Untitled view' : name,
    camera,
    createdAt: createdAt ?? 0,
  };
}

/**
 * Read a scope's views. `null` means "nothing usable stored" — the caller's
 * response to that and to a corrupt payload is identical (start empty), so they
 * are deliberately not distinguished.
 */
export function readViews(scope: ViewsScope): NamedView[] | null {
  const storage = safeStorage();
  if (storage === null) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(storageKey(scope));
  } catch {
    return null;
  }
  if (raw === null || raw === '') return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.views)) return null;

  const seen = new Set<string>();
  const out: NamedView[] = [];
  for (const entry of parsed.views as unknown[]) {
    const view = parseView(entry, seen);
    if (view !== null) out.push(view);
    if (out.length >= MAX_VIEWS) break;
  }
  return out;
}

/**
 * Write a scope's views. Answers whether it landed, so the panel can say "not
 * saved" rather than pretending.
 *
 * Views whose camera the controller would not take back unchanged are dropped
 * on the way out as well as on the way in. Nothing in this feature can produce
 * one — but a future caller constructing a `SavedCamera` by hand could, and a
 * stored bookmark that lands somewhere else is precisely the failure this
 * module exists to prevent.
 */
export function writeViews(scope: ViewsScope, views: readonly NamedView[]): boolean {
  const storage = safeStorage();
  if (storage === null) return false;
  const payload = {
    views: views.filter((view) => isStorableCamera(view.camera)).slice(0, MAX_VIEWS),
  };
  try {
    storage.setItem(storageKey(scope), JSON.stringify(payload));
    return true;
  } catch {
    // Quota, private mode, storage disabled. The panel keeps working; the list
    // simply will not survive the tab.
    return false;
  }
}

/** Forget a scope's views. Used by "delete all" in the panel. */
export function clearViews(scope: ViewsScope): boolean {
  const storage = safeStorage();
  if (storage === null) return false;
  try {
    storage.removeItem(storageKey(scope));
    return true;
  } catch {
    return false;
  }
}
