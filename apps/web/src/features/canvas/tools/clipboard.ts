/**
 * clipboard.ts — copy, paste, duplicate, mirror and array, as ONE undo each.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE PLANNERS ARE THE MODEL'S; THIS FILE ONLY DISPATCHES THEM
 * ════════════════════════════════════════════════════════════════════════════
 * `@garh/model`'s `transform.ts` plans a paste, an array or a mirror as a list
 * of ops the taxonomy already has — verified on a fork, ids derived from the
 * group id, golden-pinned against the Python twin. Nothing in `apps/web`
 * imported it. This module is that import, and it is shaped like
 * `storeys/actions.ts`: plan against the live document, ONE `dispatch` under
 * the plan's own group id, select what was created, toast with an Undo.
 * `clipboard.test.ts` asserts the undo stack grows by exactly one per gesture.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT THE CLIPBOARD HOLDS, HONESTLY
 * ════════════════════════════════════════════════════════════════════════════
 * Element IDS and an anchor, not geometry. `planPaste` resolves the ids
 * against the document at paste time, which is what lets a paste land on
 * another storey and what keeps this file free of a second copy of the
 * transform maths. The cost is stated rather than hidden: copy, delete, paste
 * is refused ("no longer in this design") instead of resurrecting the deleted
 * elements. A geometry clipboard is the obvious next step and would be a
 * model-core change, not a UI one.
 *
 * The clipboard is per tab and in memory: it never reaches the op log, the
 * server, or the system clipboard.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHERE A PASTE LANDS
 * ════════════════════════════════════════════════════════════════════════════
 * The copy remembers the selection's bounding-box corner (its ANCHOR); the
 * paste puts that corner at the last pointer position the tool layer saw,
 * snapped to the module, so ⌘C on a wall pair and ⌘V with the mouse over the
 * next bay does what an architect means by "paste here". With no pointer
 * position known (a paste from the keyboard with the mouse off the canvas)
 * the copy lands one duplicate-offset from the original, which is also what
 * ⌘D does — never exactly on top, because the model refuses that (and says
 * why: a column stacked on a column doubles the structural count silently).
 */

import { create } from 'zustand';

import {
  bbox,
  newId,
  planArray,
  planMirror,
  planPaste,
  stairFootprintPolygon,
  type ArrayRequest,
  type HouseModel,
  type MirrorAxis,
  type Op,
  type Pt,
  type TransformPlan,
  type TransformPlanResult,
  type TransformRefusal,
} from '@garh/model';

import { snapMm } from '../../../lib/units';
import { useModelStore } from '../../../stores/model';
import { useSelectionStore } from '../../../stores/selection';
import { useUiStore } from '../../../stores/ui';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Where a duplicate (or a blind paste) lands relative to its original: two
 * modules east and two south. Diagonal, so a duplicated wall never lies along
 * its original (`WALL_DUPLICATE`) and the copy is visibly a copy.
 */
export const DUPLICATE_OFFSET_MM: Pt = { x: 230, y: -230 };

// ---------------------------------------------------------------------------
// The clipboard store
// ---------------------------------------------------------------------------

export interface ClipboardEntry {
  readonly elementIds: readonly string[];
  readonly sourceStoreyId: string;
  /** Bounding-box corner (min x, min y) of the copied selection, integer mm. */
  readonly anchorMm: Pt;
}

export interface ClipboardState {
  readonly entry: ClipboardEntry | null;
  /** Last pointer position the tool layer saw on the plan, snapped. */
  readonly cursorMm: Pt | null;
  readonly setEntry: (entry: ClipboardEntry | null) => void;
  readonly noteCursor: (pt: Pt | null) => void;
}

export const useClipboardStore = create<ClipboardState>()((set, get) => ({
  entry: null,
  cursorMm: null,
  setEntry: (entry) => set({ entry }),
  noteCursor: (pt) => {
    // Written on every coalesced pointer move; a no-op write would still wake
    // every subscriber, so compare first.
    const current = get().cursorMm;
    if (pt === null ? current === null : current?.x === pt.x && current.y === pt.y) return;
    set({ cursorMm: pt });
  },
}));

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Every point a set of elements occupies — the same families `transform.ts`
 * can move. Rooms and slabs contribute nothing (they are derived).
 */
export function elementPoints(house: HouseModel, ids: readonly string[]): Pt[] {
  const wanted = new Set(ids);
  const pts: Pt[] = [];
  for (const w of house.walls) if (wanted.has(w.id)) pts.push(w.a, w.b);
  for (const s of house.stairs) if (wanted.has(s.id)) pts.push(...stairFootprintPolygon(s));
  for (const c of house.columns) if (wanted.has(c.id)) pts.push(c.pt);
  for (const f of house.furniture) if (wanted.has(f.id)) pts.push(f.pt);
  for (const b of house.balconies) if (wanted.has(b.id)) pts.push(...b.polygon);
  return pts;
}

/** The bounding-box corner a paste is positioned by, or null for nothing. */
export function selectionAnchorMm(house: HouseModel, ids: readonly string[]): Pt | null {
  const pts = elementPoints(house, ids);
  if (pts.length === 0) return null;
  const box = bbox(pts);
  return { x: box.minX, y: box.minY };
}

/** The delta that puts `anchor` at `target`, snapped to the module. */
export function pasteDeltaMm(anchor: Pt, target: Pt, moduleMm: number): Pt {
  return {
    x: snapMm(target.x - anchor.x, moduleMm),
    y: snapMm(target.y - anchor.y, moduleMm),
  };
}

/** Ids the plan creates, in op order — what to select after it lands. */
export function createdIds(ops: readonly Op[]): string[] {
  const ids: string[] = [];
  for (const op of ops) {
    switch (op.type) {
      case 'wall.add':
      case 'opening.add':
      case 'stair.add':
        ids.push(op.payload.id);
        break;
      case 'column.set':
        if (op.payload.action === 'add') ids.push(op.payload.id);
        break;
      case 'furniture.set':
        if (op.payload.action === 'place') ids.push(op.payload.id);
        break;
      case 'balcony.set':
        if (op.payload.action === 'add') ids.push(op.payload.id);
        break;
      default:
        break;
    }
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Outcomes
// ---------------------------------------------------------------------------

export type TransformOutcome =
  | { readonly ok: true; readonly plan: TransformPlan }
  | { readonly ok: false; readonly refusal: TransformRefusal };

function refused(reason: TransformRefusal['reason'], message: string): TransformOutcome {
  return { ok: false, refusal: { reason, message, issues: [] } };
}

function toast(input: {
  tone: 'info' | 'error' | 'success';
  title: string;
  description?: string | null;
  undo?: boolean;
}): void {
  useUiStore.getState().pushToast({
    tone: input.tone,
    title: input.title,
    description: input.description ?? null,
    action:
      input.undo === true
        ? { label: 'Undo', run: () => void useModelStore.getState().undo() }
        : null,
    dedupeKey: 'clipboard',
  });
}

/**
 * ONE dispatch under the plan's own group id — the ids in the ops were derived
 * from it, and the undo entry is the gesture. Selects what was created.
 */
function dispatchPlan(result: TransformPlanResult): TransformOutcome {
  if (!result.ok) {
    toast({ tone: 'error', title: result.refusal.message });
    return result;
  }
  const plan = result.plan;
  const outcome = useModelStore
    .getState()
    .dispatch(plan.ops, { label: plan.label, source: 'manual', groupId: plan.groupId });
  if (!outcome.ok) {
    const message =
      outcome.issues[0]?.message ?? 'The design changed while that was being prepared.';
    toast({ tone: 'error', title: message });
    return refused('rejected', message);
  }
  const ids = createdIds(plan.ops);
  if (ids.length > 0) useSelectionStore.getState().selectMany(ids);
  toast({ tone: 'success', title: plan.label, undo: true });
  return { ok: true, plan };
}

// ---------------------------------------------------------------------------
// The gestures
// ---------------------------------------------------------------------------

/** ⌘C: remember the selection and where it sits. Changes nothing. */
export function runCopy(): ClipboardEntry | null {
  const ids = useSelectionStore.getState().ids;
  const house = useModelStore.getState().doc.house;
  const anchor = selectionAnchorMm(house, ids);
  if (anchor === null) {
    toast({
      tone: 'info',
      title: 'Nothing to copy.',
      description: 'Select walls, columns, stairs, furniture or balconies first.',
    });
    return null;
  }
  const storeyId =
    house.walls.find((w) => ids.includes(w.id))?.storeyId ??
    house.columns.find((c) => ids.includes(c.id))?.storeyId ??
    house.stairs.find((s) => ids.includes(s.id))?.storeyId ??
    house.furniture.find((f) => ids.includes(f.id))?.storeyId ??
    house.balconies.find((b) => ids.includes(b.id))?.storeyId ??
    null;
  if (storeyId === null) return null;
  const entry: ClipboardEntry = {
    elementIds: [...ids],
    sourceStoreyId: storeyId,
    anchorMm: anchor,
  };
  useClipboardStore.getState().setEntry(entry);
  toast({
    tone: 'info',
    title: `Copied ${String(ids.length)} ${ids.length === 1 ? 'item' : 'items'}.`,
    description: 'Paste with ⌘V where the pointer is, or ⌘D to duplicate in place.',
  });
  return entry;
}

/**
 * ⌘V: paste the clipboard onto the active storey, anchor at the pointer.
 * `targetMm` overrides the remembered pointer (a test, a menu at a point).
 */
export function runPaste(targetMm?: Pt | null): TransformOutcome {
  const clipboard = useClipboardStore.getState();
  const entry = clipboard.entry;
  if (entry === null) {
    toast({
      tone: 'info',
      title: 'Nothing copied yet.',
      description: 'Select something and press ⌘C.',
    });
    return refused('empty-selection', 'Nothing copied yet.');
  }
  const ui = useUiStore.getState();
  const doc = useModelStore.getState().doc;
  const target = targetMm === undefined ? clipboard.cursorMm : targetMm;
  const moduleMm = ui.snapMode === 'module' ? 115 : ui.snapMode === 'fine' ? 25 : 0;
  let deltaMm: Pt =
    target === null ? DUPLICATE_OFFSET_MM : pasteDeltaMm(entry.anchorMm, target, moduleMm);
  const targetStoreyId = ui.activeStoreyId ?? entry.sourceStoreyId;
  // A zero delta on the source storey is the model's zero-offset refusal; on a
  // paste the architect meant "here", so nudge rather than refuse.
  if (deltaMm.x === 0 && deltaMm.y === 0 && targetStoreyId === entry.sourceStoreyId) {
    deltaMm = DUPLICATE_OFFSET_MM;
  }
  return dispatchPlan(
    planPaste(doc, {
      elementIds: entry.elementIds,
      deltaMm,
      targetStoreyId,
      groupId: newId('group'),
    }),
  );
}

/** ⌘D: a copy of the selection, one offset over. The clipboard is untouched. */
export function runDuplicate(): TransformOutcome {
  const ids = useSelectionStore.getState().ids;
  if (ids.length === 0) {
    toast({ tone: 'info', title: 'Nothing to duplicate.', description: 'Select something first.' });
    return refused('empty-selection', 'Nothing selected.');
  }
  const doc = useModelStore.getState().doc;
  return dispatchPlan(
    planPaste(doc, { elementIds: ids, deltaMm: DUPLICATE_OFFSET_MM, groupId: newId('group') }),
  );
}

export interface MirrorOptions {
  readonly axis: MirrorAxis;
  /** Absent = through the selection's own centre (the CAD default). */
  readonly atMm?: number | null;
  /** Default true: keep the originals and add a mirrored copy. */
  readonly keepOriginal?: boolean;
}

/** Mirror the selection across an axis-aligned line. */
export function runMirror(options: MirrorOptions): TransformOutcome {
  const ids = useSelectionStore.getState().ids;
  if (ids.length === 0) {
    toast({ tone: 'info', title: 'Nothing to mirror.', description: 'Select something first.' });
    return refused('empty-selection', 'Nothing selected.');
  }
  const doc = useModelStore.getState().doc;
  return dispatchPlan(
    planMirror(doc, {
      elementIds: ids,
      axis: options.axis,
      atMm: options.atMm ?? null,
      keepOriginal: options.keepOriginal ?? true,
      groupId: newId('group'),
    }),
  );
}

export type ArrayOptions = Omit<ArrayRequest, 'elementIds' | 'groupId'>;

/** Array the selection as a grid; counts include the original. */
export function runArray(options: ArrayOptions): TransformOutcome {
  const ids = useSelectionStore.getState().ids;
  if (ids.length === 0) {
    toast({ tone: 'info', title: 'Nothing to array.', description: 'Select something first.' });
    return refused('empty-selection', 'Nothing selected.');
  }
  const doc = useModelStore.getState().doc;
  return dispatchPlan(planArray(doc, { ...options, elementIds: ids, groupId: newId('group') }));
}

/**
 * What an array WOULD do, for the dialog to describe before the click. Pure
 * against the live document; the dialog and the dispatch cannot disagree.
 */
export function previewArray(options: ArrayOptions): TransformPlanResult {
  const ids = useSelectionStore.getState().ids;
  const doc = useModelStore.getState().doc;
  return planArray(doc, { ...options, elementIds: ids, groupId: newId('group') });
}
