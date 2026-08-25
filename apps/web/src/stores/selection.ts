/**
 * `selection` — what is currently picked, in element ids (§12).
 *
 * Ids, never objects. The model document is replaced wholesale on every op, so
 * a stored `Wall` reference would be a stale copy within one keystroke of being
 * captured. Ids survive edits; that is exactly why `@garh/model` works so hard
 * to preserve room ids across a re-solve (§3), and the selection is the first
 * thing that would break if it did not.
 *
 * 2D and 3D share this store. §12 asks for "shared raycast hit-testing;
 * selection state common to both" — one store is how that is true rather than
 * aspirational.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * PHASE 4: HIT RESULTS
 * ────────────────────────────────────────────────────────────────────────────
 * The canvas picker (`features/canvas/core/hitTest`) resolves a raycast to a
 * `{ kind, id, storeyId }` plus the model point under the cursor. The extra
 * facts are worth keeping for two reasons:
 *
 *  - **`kind` is not always derivable from the id.** `idType()` reads the id
 *    prefix, which answers "wall or room?" but not "the wall, or the dimension
 *    string measuring it?". Dimensions and room-tag handles carry synthetic
 *    pick ids that no `ElementType` covers, and the inspector must be able to
 *    tell them apart from the element they point at.
 *  - **`pointMm` is where the click landed**, which is what a context menu and
 *    a "insert vertex here" action need. Recomputing it later would mean
 *    keeping a camera matrix somewhere it does not belong.
 *
 * The kind map is a side table keyed by id rather than a richer `ids` array so
 * that every existing selector (`ids`, `selectPrimaryId`, `selectIsSelected`)
 * keeps working unchanged and cheaply — a selection of ids is still an array of
 * strings, and nothing that only cares about "what is selected" pays for the
 * extra facts.
 *
 * `kind` is imported **type-only** from the canvas core so this store stays a
 * leaf: nothing here imports three.js, and `stores/` still has no runtime
 * dependency on `features/`.
 */

import { create } from 'zustand';

import { idType, type ElementType, type Pt } from '@garh/model';

import type { PickKind } from '../features/canvas/core/constants';

/** A rubber-band rectangle in plot-local integer mm. */
export interface MarqueeRect {
  readonly ax: number;
  readonly ay: number;
  readonly bx: number;
  readonly by: number;
}

/**
 * One resolved pick, flattened out of the canvas core's `PickHit`.
 *
 * Structurally a subset of `PickHit`, so `selectHit(core.pick(ndc))` and
 * `selectHit(event.hit())` both type-check with no adapter. The three.js
 * object and the raycast distance are deliberately dropped: nothing outside
 * the renderer may hold a reference to a mesh that the next document change
 * will dispose.
 */
export interface SelectionHit {
  readonly kind: PickKind;
  /** `null` when the ray hit nothing — an empty-space click. */
  readonly id: string | null;
  readonly storeyId: string | null;
  /** Model point under the cursor, integer mm. `null` if the ray missed. */
  readonly pointMm: Pt | null;
}

/** How a hit combines with what is already selected. */
export type SelectMode = 'replace' | 'toggle' | 'add';

export interface SelectionState {
  /** Ordered; `ids[0]` is the primary selection the inspector shows. */
  ids: string[];
  /**
   * `id → PickKind` for everything currently selected or hovered. Pruned with
   * the selection, so it never outlives the ids it describes.
   */
  kinds: Readonly<Record<string, PickKind>>;
  hoverId: string | null;
  /** The full hover hit, for tools and the status readout. */
  hover: SelectionHit | null;
  /** The last click's model point — context menus and "insert here" actions. */
  lastPointMm: Pt | null;
  marquee: MarqueeRect | null;

  // ── actions ────────────────────────────────────────────────────────────
  /** Replace the selection with one element (or clear it with `null`). */
  select: (id: string | null) => void;
  /** Replace the selection with a set, order preserved, duplicates dropped. */
  selectMany: (ids: readonly string[]) => void;
  /** Add if absent, remove if present — the Shift/Cmd-click behaviour. */
  toggle: (id: string) => void;
  add: (ids: readonly string[]) => void;
  remove: (ids: readonly string[]) => void;
  clear: () => void;

  /**
   * Apply a canvas pick. A hit with a `null` id in `replace` mode clears the
   * selection — clicking empty paper deselects, which is what every drawing
   * program does and what the select tool relies on.
   */
  selectHit: (hit: SelectionHit, mode?: SelectMode) => void;
  /** Record kinds for ids selected through a non-canvas path (a chip click). */
  noteKinds: (entries: Readonly<Record<string, PickKind>>) => void;

  setHover: (id: string | null) => void;
  setHoverHit: (hit: SelectionHit | null) => void;
  setMarquee: (rect: MarqueeRect | null) => void;

  /**
   * Drop ids that no longer exist. Called by the model store after every doc
   * change — an undo, a rebase, or a solver apply can all delete the wall the
   * inspector is editing, and an inspector bound to a ghost is a crash.
   */
  prune: (existing: ReadonlySet<string>) => void;
}

function dedupe(ids: readonly string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/** Keep only the kinds we still have a use for: selected ids plus the hover. */
function pruneKinds(
  kinds: Readonly<Record<string, PickKind>>,
  ids: readonly string[],
  hoverId: string | null,
): Readonly<Record<string, PickKind>> {
  const keep = new Set(ids);
  if (hoverId !== null) keep.add(hoverId);
  let changed = false;
  const out: Record<string, PickKind> = {};
  for (const [id, kind] of Object.entries(kinds)) {
    if (keep.has(id)) out[id] = kind;
    else changed = true;
  }
  return changed ? out : kinds;
}

export const useSelectionStore = create<SelectionState>()((set, get) => ({
  ids: [],
  kinds: {},
  hoverId: null,
  hover: null,
  lastPointMm: null,
  marquee: null,

  select: (id) =>
    set((s) => {
      const ids = id === null ? [] : [id];
      return { ids, kinds: pruneKinds(s.kinds, ids, s.hoverId) };
    }),

  selectMany: (ids) =>
    set((s) => {
      const next = dedupe(ids);
      return { ids: next, kinds: pruneKinds(s.kinds, next, s.hoverId) };
    }),

  toggle: (id) =>
    set((s) => {
      const ids = s.ids.includes(id) ? s.ids.filter((x) => x !== id) : [...s.ids, id];
      return { ids, kinds: pruneKinds(s.kinds, ids, s.hoverId) };
    }),

  add: (ids) =>
    set((s) => {
      const next = dedupe([...s.ids, ...ids]);
      return { ids: next, kinds: pruneKinds(s.kinds, next, s.hoverId) };
    }),

  remove: (ids) => {
    const drop = new Set(ids);
    set((s) => {
      const next = s.ids.filter((x) => !drop.has(x));
      return { ids: next, kinds: pruneKinds(s.kinds, next, s.hoverId) };
    });
  },

  clear: () => set((s) => ({ ids: [], kinds: pruneKinds(s.kinds, [], s.hoverId), marquee: null })),

  selectHit: (hit, mode = 'replace') => {
    const s = get();
    if (hit.pointMm !== null) set({ lastPointMm: hit.pointMm });

    if (hit.id === null) {
      // Empty paper. `replace` deselects; the additive modes leave the
      // selection alone rather than making a shift-click on nothing destructive.
      if (mode === 'replace') s.clear();
      return;
    }

    const kinds = { ...s.kinds, [hit.id]: hit.kind };
    const ids =
      mode === 'replace'
        ? [hit.id]
        : mode === 'add'
          ? dedupe([...s.ids, hit.id])
          : s.ids.includes(hit.id)
            ? s.ids.filter((x) => x !== hit.id)
            : [...s.ids, hit.id];

    set({ ids, kinds: pruneKinds(kinds, ids, s.hoverId) });
  },

  noteKinds: (entries) =>
    set((s) => ({ kinds: pruneKinds({ ...s.kinds, ...entries }, s.ids, s.hoverId) })),

  setHover: (id) => {
    // Early return, not a `{}` patch: zustand notifies every subscriber for an
    // empty patch too, and this is called from the hover path.
    if (get().hoverId === id) return;
    set({ hoverId: id, hover: null });
  },

  setHoverHit: (hit) => {
    const s = get();
    const nextId = hit?.id ?? null;
    // Hover fires once per *change* from `CanvasRoot`, but a caller may still
    // hand us the same target twice (a re-render, a storey switch). Writing
    // anyway would notify every subscriber for nothing, sixty times a second.
    if (s.hoverId === nextId && (nextId !== null || s.hover === null)) return;
    const kinds =
      hit === null || hit.id === null ? s.kinds : { ...s.kinds, [hit.id]: hit.kind };
    set({
      hoverId: nextId,
      hover: nextId === null ? null : hit,
      kinds: pruneKinds(kinds, s.ids, nextId),
    });
  },

  setMarquee: (rect) => set({ marquee: rect }),

  prune: (existing) => {
    const s = get();
    const kept = s.ids.filter((id) => existing.has(id));
    const hoverGone = s.hoverId !== null && !existing.has(s.hoverId);
    // Only write when something actually changed: a no-op `set` still notifies
    // every subscriber, and this runs after every single op.
    if (kept.length === s.ids.length && !hoverGone) return;
    const hoverId = hoverGone ? null : s.hoverId;
    set({
      ids: kept,
      kinds: pruneKinds(s.kinds, kept, hoverId),
      ...(hoverGone ? { hoverId: null, hover: null } : {}),
    });
  },
}));

// ---------------------------------------------------------------------------
// Selectors
// ---------------------------------------------------------------------------

export const selectSelectedIds = (s: SelectionState): string[] => s.ids;

/** The element the inspector edits. */
export const selectPrimaryId = (s: SelectionState): string | null => s.ids[0] ?? null;

export const selectHoverId = (s: SelectionState): string | null => s.hoverId;

export const selectHoverHit = (s: SelectionState): SelectionHit | null => s.hover;

export const selectSelectionCount = (s: SelectionState): number => s.ids.length;

/** Curried membership test — `useSelectionStore(selectIsSelected(wall.id))`. */
export const selectIsSelected =
  (id: string) =>
  (s: SelectionState): boolean =>
    s.ids.includes(id);

/** Ids of one element family, e.g. `selectIdsOfType('room')`. */
export const selectIdsOfType =
  (type: ElementType) =>
  (s: SelectionState): string[] =>
    s.ids.filter((id) => idType(id) === type);

/**
 * The single element type currently selected, or null for an empty or mixed
 * selection — which is what the inspector switches on.
 */
export const selectPrimaryType = (s: SelectionState): ElementType | null => {
  const first = s.ids[0];
  if (first === undefined) return null;
  const type = idType(first);
  if (type === null) return null;
  for (const id of s.ids) {
    if (idType(id) !== type) return null;
  }
  return type;
};

/**
 * The pick kind recorded for an id, or null when it was selected through a
 * path that never saw a raycast. Falls back to nothing on purpose — guessing
 * from the id prefix is what `selectPrimaryType` is for, and conflating the
 * two is how a dimension handle ends up in the wall inspector.
 */
export const selectKindOf =
  (id: string | null) =>
  (s: SelectionState): PickKind | null =>
    id === null ? null : (s.kinds[id] ?? null);

/** The kind of the primary selection — what the canvas inspector switches on. */
export const selectPrimaryKind = (s: SelectionState): PickKind | null => {
  const first = s.ids[0];
  return first === undefined ? null : (s.kinds[first] ?? null);
};
