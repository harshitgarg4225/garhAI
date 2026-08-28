/**
 * persist.ts — favourites and recently-used, in `localStorage`, per user.
 *
 * WHY LOCAL AND NOT THE SERVER. Neither list is a property of a design. They
 * change no geometry, appear in no op, and must never reach the op log or a
 * collaborator's screen — two architects in one firm want different pins, and
 * syncing them would be a bug rather than a feature. This is browser state, and
 * losing it costs one re-pin.
 *
 * WHY PER USER AND NOT PER PROJECT. The opposite call to
 * `features/layers/persist.ts`, and for the opposite reason: which layers you
 * hid is about a drawing, but "I always use the 1800 mm sliding wardrobe" is
 * about a practice. It should follow you into the next project. A shared studio
 * machine is normal, so the key still carries the user id.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * EVERY ACCESS IS WRAPPED, AND THAT IS NOT DEFENSIVE PROGRAMMING THEATRE
 * ════════════════════════════════════════════════════════════════════════════
 * `localStorage` does not merely return null when it is unavailable. Safari in
 * private browsing has historically thrown `QuotaExceededError` on every
 * `setItem`; Chrome throws `SecurityError` on the mere PROPERTY ACCESS when
 * third-party cookies are blocked and the page is framed — which is exactly how
 * a share link gets embedded. Touching `globalThis.localStorage` at all is the
 * throwing operation, so the guard has to be around that, not only around the
 * call. `persist.test.ts` proves it with a storage that throws on the property
 * itself, and negative-tests the guard by removing it.
 *
 * The contract offered to the store is total: reading always answers a list
 * (possibly empty), writing never throws, and a browser with storage switched
 * off gets a fully working asset browser that forgets between sessions.
 */

/** Bumped when the stored shape changes; an older payload is then ignored, not migrated. */
const STORAGE_VERSION = 1;

const KEY_PREFIX = `garh:assets:v${STORAGE_VERSION}`;

/**
 * How many pins are kept. Well past any real user (the whole library is 653),
 * and small enough that the JSON blob stays a few kilobytes on a quota that is
 * shared with the layer state and the theme.
 */
export const FAVOURITES_MAX = 300;

/**
 * How many recents are kept.
 *
 * Twelve, because "recently used" is a shortcut, not a history. A list long
 * enough to need its own search has stopped being one.
 */
export const RECENTS_MAX = 12;

export function favouritesKey(userId: string): string {
  return `${KEY_PREFIX}:favourites:${userId}`;
}

export function recentsKey(userId: string): string {
  return `${KEY_PREFIX}:recents:${userId}`;
}

/**
 * The storage object, or null when reaching for it throws or it is absent.
 *
 * Separate from the read/write helpers because the property access is itself
 * the dangerous part (see the header) and both helpers need it guarded.
 */
function safeStorage(): Storage | null {
  try {
    const storage = globalThis.localStorage;
    return storage ?? null;
  } catch {
    return null;
  }
}

/**
 * Coerce a stored payload into a clean list of asset keys.
 *
 * Anything that is not an array of non-empty strings yields an empty list, and
 * duplicates are dropped. Never trust a shape you did not construct in this
 * process — a payload written by an older build, or hand-edited in devtools,
 * must not be able to put `undefined` into a `Set` the renderer keys off.
 */
function coerceKeys(raw: unknown, max: number): readonly string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const value of raw) {
    if (typeof value !== 'string' || value === '') continue;
    if (seen.has(value)) continue;
    seen.add(value);
    out.push(value);
    if (out.length >= max) break;
  }
  return out;
}

function readKeys(storageKey: string, max: number): readonly string[] {
  const storage = safeStorage();
  if (storage === null) return [];
  let raw: string | null;
  try {
    raw = storage.getItem(storageKey);
  } catch {
    return [];
  }
  if (raw === null || raw === '') return [];
  try {
    return coerceKeys(JSON.parse(raw), max);
  } catch {
    // Corrupt JSON and "nothing stored" get the same answer on purpose: the
    // only sensible response to either is an empty list.
    return [];
  }
}

function writeKeys(storageKey: string, keys: readonly string[]): void {
  const storage = safeStorage();
  if (storage === null) return;
  try {
    storage.setItem(storageKey, JSON.stringify(keys));
  } catch {
    // Quota exhausted, or storage disabled between the read and the write.
    // Losing the memory is acceptable; throwing out of a click handler and
    // unmounting the panel is not.
  }
}

export function readFavourites(userId: string): readonly string[] {
  return readKeys(favouritesKey(userId), FAVOURITES_MAX);
}

export function writeFavourites(userId: string, keys: readonly string[]): void {
  writeKeys(favouritesKey(userId), keys.slice(0, FAVOURITES_MAX));
}

export function readRecents(userId: string): readonly string[] {
  return readKeys(recentsKey(userId), RECENTS_MAX);
}

export function writeRecents(userId: string, keys: readonly string[]): void {
  writeKeys(recentsKey(userId), keys.slice(0, RECENTS_MAX));
}

/** Forget both lists for one user — called on sign-out. */
export function clearAssetPrefs(userId: string): void {
  const storage = safeStorage();
  if (storage === null) return;
  try {
    storage.removeItem(favouritesKey(userId));
    storage.removeItem(recentsKey(userId));
  } catch {
    // Same reasoning as writeKeys.
  }
}

// ---------------------------------------------------------------------------
// Pure list operations — the store's reducers, testable without a browser
// ---------------------------------------------------------------------------

/** Add or remove `key`, newest pin first, capped at {@link FAVOURITES_MAX}. */
export function toggleFavourite(list: readonly string[], key: string): readonly string[] {
  if (list.includes(key)) return list.filter((k) => k !== key);
  return [key, ...list].slice(0, FAVOURITES_MAX);
}

/** Move `key` to the front, capped at {@link RECENTS_MAX}. Re-use does not duplicate. */
export function pushRecent(list: readonly string[], key: string): readonly string[] {
  return [key, ...list.filter((k) => k !== key)].slice(0, RECENTS_MAX);
}
