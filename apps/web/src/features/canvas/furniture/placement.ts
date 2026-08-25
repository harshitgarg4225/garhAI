/**
 * The furniture tool as a state machine — `idle → preview → commit(op)`, with
 * Esc cancelling, Enter committing and a typed number overriding the mouse
 * (§12 requires all three of every tool).
 *
 *      idle ──arm(item)──▶ placing ──commit()──▶ (ops) ──▶ placing | idle
 *        ▲                    │                                    │
 *        └────── cancel() ────┴──────── Esc ───────────────────────┘
 *
 *      idle ──beginMove(id)─▶ moving ──commit()──▶ (ops) ──▶ idle
 *
 * `placing` stays armed after a commit so an architect can drop six dining
 * chairs without going back to the browser each time; Esc (or switching tools)
 * disarms.
 *
 * ## Why a class and not a reducer in React state
 *
 * §14 budgets 16 ms a frame during a drag. A React `setState` per pointer move
 * re-renders the browser panel, the HUD and anything else subscribed — every
 * move, at pointer rate. So the controller keeps the pose in a mutable field
 * and publishes on TWO channels:
 *
 *   `subscribe()`      COARSE. Fires only when something a React tree cares
 *                      about changes: the phase, which item is armed, the
 *                      numeric-entry buffer. A handful of events per placement.
 *   `subscribePose()`  FINE. Fires on every pose change. Consumed only by
 *                      imperative subscribers — the Three.js preview writes a
 *                      matrix, the HUD writes `textContent`. No React involved.
 *
 * That split is what lets the preview track the cursor at pointer rate while
 * React renders roughly five times in an entire placement.
 *
 * ## Numeric entry (the §12 "type an exact value" rule, for furniture)
 *
 * A wall tool types a length. Furniture has no length to type, so the tool
 * types the three numbers that actually describe a placement:
 *
 *   digits          → rotation in degrees ("45" Enter = 45°)
 *   X then digits   → exact plan X of the centre, in project units ("12'6\"")
 *   Y then digits   → exact plan Y of the centre
 *
 * Coordinates parse through `parseLengthMm`, so `3.8m`, `12'6"`, `3800` and
 * `12 ft` all work — the same parser the rest of the app uses (golden rule 6).
 * Esc clears a live buffer before it cancels the placement, which is what makes
 * a mistyped number recoverable without losing the pose.
 *
 * ## Nothing here mutates the model
 *
 * `commit()` RETURNS ops. `useFurniturePlacement` dispatches them. Golden rule
 * 1 stays visible: this file cannot write to the document even by accident.
 */

import { tryParseLengthMm, type Op, type Pt } from '@garh/model';

import {
  EMPTY_CONTEXT,
  evaluatePlacement,
  issueTone,
  type PlacementContext,
} from './collision';
import { angleFromDrag, normaliseRotationDeg, rotateBy, snapPtMm } from './geometry';
import {
  deleteFurnitureOp,
  deleteLabel,
  moveLabel,
  newFurnitureId,
  placeFurnitureOp,
  placeLabel,
  transformFurnitureOp,
} from './ops';
import type { CatalogueItem, PlacementIssue, Pose } from './types';

export type PlacementPhase = 'idle' | 'placing' | 'moving';

/** Which number a live numeric entry is filling in. */
export type EntryTarget = 'rotation' | 'x' | 'y';

export interface NumericEntry {
  readonly target: EntryTarget;
  readonly buffer: string;
  /** Parsed value, or null while the buffer is empty or unparseable. */
  readonly valueMm: number | null;
}

/** What React renders. Changes a handful of times per placement. */
export interface PlacementCoarseState {
  readonly phase: PlacementPhase;
  readonly item: CatalogueItem | null;
  /** The instance being dragged, in `moving`. */
  readonly instanceId: string | null;
  readonly entry: NumericEntry | null;
}

/** What the preview renders. Changes at pointer rate. */
export interface PlacementPoseState {
  readonly phase: PlacementPhase;
  readonly item: CatalogueItem | null;
  readonly pose: Pose;
  readonly issues: readonly PlacementIssue[];
  readonly tone: 'ok' | 'info' | 'warn';
  /** True while a modifier is held and the pointer is steering the angle. */
  readonly freeRotating: boolean;
}

/** Pointer modifiers, passed in rather than read from an event: testable. */
export interface PointerModifiers {
  /** Alt/Option — free-rotate about the placement centre. */
  readonly alt?: boolean | undefined;
  /** Shift — coarsen free rotation to 15°, and reverse R. */
  readonly shift?: boolean | undefined;
}

export interface CommitResult {
  readonly ops: readonly Op[];
  /** Undo-toast copy, sentence case, no trailing period (§15). */
  readonly label: string;
  /** The instance the ops act on — so the caller can select it. */
  readonly furnitureId: string;
}

const ORIGIN: Pt = { x: 0, y: 0 };
/** Shift + free-rotate snaps to this, for quick 15° steps. */
const COARSE_FREE_ROTATE_DEG = 15;

export class PlacementController {
  private phase: PlacementPhase = 'idle';
  private item: CatalogueItem | null = null;
  private instanceId: string | null = null;
  private pt: Pt = ORIGIN;
  private rotationDeg = 0;
  private entry: NumericEntry | null = null;
  private issues: readonly PlacementIssue[] = [];
  private freeRotating = false;
  private ctx: PlacementContext = EMPTY_CONTEXT;

  private readonly coarseListeners = new Set<(s: PlacementCoarseState) => void>();
  private readonly poseListeners = new Set<(s: PlacementPoseState) => void>();

  /**
   * Cached coarse snapshot.
   *
   * `useSyncExternalStore` compares snapshots with `Object.is` and re-renders
   * forever if the getter mints a new object each call. Caching it, and only
   * replacing it when a field genuinely changed, is what makes the React side
   * of this controller safe — and it is also what guarantees the "a handful of
   * renders per placement" claim above, rather than merely hoping for it.
   */
  private coarseSnapshot: PlacementCoarseState = {
    phase: 'idle',
    item: null,
    instanceId: null,
    entry: null,
  };

  // ── subscriptions ───────────────────────────────────────────────────────

  /** COARSE channel. Safe for React; fires only on phase/item/entry changes. */
  subscribe = (listener: (state: PlacementCoarseState) => void): (() => void) => {
    this.coarseListeners.add(listener);
    return () => {
      this.coarseListeners.delete(listener);
    };
  };

  /** FINE channel. Imperative subscribers only — never call setState from here. */
  subscribePose = (listener: (state: PlacementPoseState) => void): (() => void) => {
    this.poseListeners.add(listener);
    return () => {
      this.poseListeners.delete(listener);
    };
  };

  /** Stable across calls until something changes. See {@link coarseSnapshot}. */
  getCoarseState = (): PlacementCoarseState => this.coarseSnapshot;

  getPoseState(): PlacementPoseState {
    return {
      phase: this.phase,
      item: this.item,
      pose: { pt: this.pt, rotationDeg: this.rotationDeg },
      issues: this.issues,
      tone: issueTone(this.issues),
      freeRotating: this.freeRotating,
    };
  }

  getContext(): PlacementContext {
    return this.ctx;
  }

  // ── context ─────────────────────────────────────────────────────────────

  /**
   * Swap in a rebuilt obstacle set (the document changed, or the storey did).
   * Re-evaluates the current pose so a preview that was clear before an undo
   * does not keep claiming to be clear afterwards.
   */
  setContext(ctx: PlacementContext): void {
    this.ctx = ctx;
    if (this.phase !== 'idle') {
      this.reevaluate();
      this.emitPose();
    }
  }

  // ── entering the machine ────────────────────────────────────────────────

  /**
   * Arm the tool with a catalogue item (browser click, or F then a pick).
   * `at` seeds the preview so it appears under the cursor rather than at the
   * origin on the first frame.
   */
  arm(item: CatalogueItem, at?: Pt, rotationDeg = 0): void {
    this.phase = 'placing';
    this.item = item;
    this.instanceId = null;
    this.pt = at === undefined ? this.pt : snapPtMm(at, this.ctx.snapStepMm);
    this.rotationDeg = normaliseRotationDeg(rotationDeg);
    this.entry = null;
    this.freeRotating = false;
    this.reevaluate();
    this.emitAll();
  }

  /**
   * Start dragging an item that already exists. The caller supplies the
   * instance's current pose — the controller never reads the document.
   */
  beginMove(instanceId: string, item: CatalogueItem, pose: Pose): void {
    this.phase = 'moving';
    this.item = item;
    this.instanceId = instanceId;
    this.pt = pose.pt;
    this.rotationDeg = normaliseRotationDeg(pose.rotationDeg);
    this.entry = null;
    this.freeRotating = false;
    this.reevaluate();
    this.emitAll();
  }

  // ── the preview follows the pointer ─────────────────────────────────────

  /**
   * A pointer move in PLOT-LOCAL MILLIMETRES.
   *
   * Screen→plan projection belongs to the canvas core, which owns the camera;
   * this feature deliberately never touches a camera matrix, which is also why
   * the same handler works for the 2D plan and, unchanged, for a Phase-5 3D
   * pick on the floor plane.
   *
   * Floats are welcome as input — {@link snapPtMm} rounds, and `ops.ts` asserts.
   */
  pointerMove(ptMm: Pt, mods: PointerModifiers = {}): void {
    if (this.phase === 'idle' || this.item === null) return;

    const alt = mods.alt === true;
    if (alt) {
      if (!this.freeRotating) {
        // Releasing Alt keeps the angle you just steered to — you rotated on
        // purpose, so snapping back would undo deliberate work.
        this.freeRotating = true;
        this.emitCoarse();
      }
      const step = mods.shift === true ? COARSE_FREE_ROTATE_DEG : 1;
      this.rotationDeg = angleFromDrag(this.pt, ptMm, step);
    } else {
      if (this.freeRotating) {
        this.freeRotating = false;
        this.emitCoarse();
      }
      // A live coordinate entry pins that axis; the pointer drives the other.
      const snapped = snapPtMm(ptMm, this.ctx.snapStepMm);
      const pinX = this.entry?.target === 'x' && this.entry.valueMm !== null;
      const pinY = this.entry?.target === 'y' && this.entry.valueMm !== null;
      this.pt = {
        x: pinX ? this.pt.x : snapped.x,
        y: pinY ? this.pt.y : snapped.y,
      };
    }

    this.reevaluate();
    this.emitPose();
  }

  /** Rotate by a whole number of degrees. R = +90, Shift-R = −90. */
  rotate(deltaDeg: number): void {
    if (this.phase === 'idle') return;
    this.rotationDeg = rotateBy(this.rotationDeg, deltaDeg);
    this.reevaluate();
    this.emitPose();
  }

  // ── leaving the machine ─────────────────────────────────────────────────

  /**
   * Commit at the current pose. Returns the ops; the caller dispatches.
   * Returns `null` when there is nothing to commit or no storey to place on —
   * never a partially-valid op.
   *
   * NOTE what is absent: no check of `this.issues`. Overlaps do not stop a
   * placement, by design (golden rule 5).
   */
  commit(): CommitResult | null {
    if (this.item === null) return null;
    const pose: Pose = { pt: this.pt, rotationDeg: this.rotationDeg };

    if (this.phase === 'placing') {
      if (this.ctx.storeyId === null) return null;
      const id = newFurnitureId();
      const result: CommitResult = {
        ops: [
          placeFurnitureOp({
            id,
            storeyId: this.ctx.storeyId,
            catalogId: this.item.id,
            pose,
          }),
        ],
        label: placeLabel(this.item),
        furnitureId: id,
      };
      // Stay armed: placing six dining chairs should cost six clicks, not
      // six trips to the browser. Clear only the numeric entry.
      this.entry = null;
      this.emitCoarse();
      return result;
    }

    if (this.phase === 'moving' && this.instanceId !== null) {
      const result: CommitResult = {
        ops: [transformFurnitureOp(this.instanceId, pose)],
        label: moveLabel(this.item),
        furnitureId: this.instanceId,
      };
      this.reset();
      return result;
    }

    return null;
  }

  /** Esc, a tool switch, or a drag that left the canvas. */
  cancel(): void {
    if (this.phase === 'idle' && this.entry === null) return;
    this.reset();
  }

  /**
   * Ops for deleting instances. Not part of the machine — deletion works on a
   * selection while the tool is idle — but it lives here so every furniture op
   * the UI can produce is reachable from one object.
   */
  deleteOps(ids: readonly string[], items: readonly (CatalogueItem | null)[]): CommitResult | null {
    const first = ids[0];
    if (first === undefined) return null;
    return {
      ops: ids.map(deleteFurnitureOp),
      label: deleteLabel(items),
      furnitureId: first,
    };
  }

  private reset(): void {
    this.phase = 'idle';
    this.item = null;
    this.instanceId = null;
    this.entry = null;
    this.issues = [];
    this.freeRotating = false;
    this.emitAll();
  }

  // ── keyboard ────────────────────────────────────────────────────────────

  /**
   * One keystroke. Returns whether the tool consumed it, so the canvas can let
   * anything else through to the global key map (V/W/D/…, undo, storey switch).
   *
   * `commit` is returned rather than applied — same rule as {@link commit}.
   */
  handleKey(event: FurnitureKeyEvent): KeyOutcome {
    if (this.phase === 'idle') return { handled: false };

    const key = event.key;

    if (key === 'Escape') {
      if (this.entry !== null) {
        // A mistyped number should cost the number, not the placement.
        this.entry = null;
        this.emitCoarse();
        return { handled: true };
      }
      this.cancel();
      return { handled: true };
    }

    if (key === 'Enter') {
      return { handled: true, commit: this.commit() };
    }

    if (key === 'r' || key === 'R') {
      this.rotate(event.shift === true ? -90 : 90);
      return { handled: true };
    }

    if (key === 'x' || key === 'X') {
      this.startEntry('x');
      return { handled: true };
    }
    if (key === 'y' || key === 'Y') {
      this.startEntry('y');
      return { handled: true };
    }

    if (key === 'Backspace') {
      if (this.entry === null) return { handled: false };
      this.setBuffer(this.entry.target, this.entry.buffer.slice(0, -1));
      return { handled: true };
    }

    const active = this.entry;
    const starts = active === null || active.buffer === '';
    if (starts ? startsEntry(key) : continuesEntry(key)) {
      const target: EntryTarget = active?.target ?? 'rotation';
      this.setBuffer(target, (active?.buffer ?? '') + key);
      return { handled: true };
    }

    return { handled: false };
  }

  private startEntry(target: EntryTarget): void {
    this.entry = { target, buffer: '', valueMm: null };
    this.emitCoarse();
  }

  /**
   * Apply a buffer to the pose as it is typed, so the preview moves while you
   * type rather than jumping on Enter. An unparseable buffer leaves the pose
   * where it was — mid-typing, `12'` is not yet a number and should not snap
   * the item to zero.
   */
  private setBuffer(target: EntryTarget, buffer: string): void {
    let valueMm: number | null = null;

    if (buffer !== '') {
      if (target === 'rotation') {
        const deg = Number.parseInt(buffer.replace(/[^\d-]/g, ''), 10);
        if (Number.isFinite(deg)) {
          valueMm = deg;
          this.rotationDeg = normaliseRotationDeg(deg);
        }
      } else {
        // `12'6"`, `3.8m`, `3800`, `12 ft` — the app's one length parser.
        const parsed = tryParseLengthMm(buffer);
        if (parsed.ok) {
          valueMm = parsed.mm;
          this.pt =
            target === 'x' ? { x: parsed.mm, y: this.pt.y } : { x: this.pt.x, y: parsed.mm };
        }
      }
    }

    this.entry = { target, buffer, valueMm };
    this.reevaluate();
    this.emitCoarse();
    this.emitPose();
  }

  // ── internals ───────────────────────────────────────────────────────────

  private reevaluate(): void {
    this.issues =
      this.item === null
        ? []
        : evaluatePlacement(this.item, { pt: this.pt, rotationDeg: this.rotationDeg }, this.ctx);
  }

  private emitCoarse(): void {
    const prev = this.coarseSnapshot;
    const entry = this.entry;
    const unchanged =
      prev.phase === this.phase &&
      prev.item === this.item &&
      prev.instanceId === this.instanceId &&
      prev.entry?.target === entry?.target &&
      prev.entry?.buffer === entry?.buffer &&
      prev.entry?.valueMm === entry?.valueMm;
    if (unchanged) return;

    this.coarseSnapshot = {
      phase: this.phase,
      item: this.item,
      instanceId: this.instanceId,
      entry,
    };
    for (const listener of this.coarseListeners) listener(this.coarseSnapshot);
  }

  private emitPose(): void {
    if (this.poseListeners.size === 0) return;
    const state = this.getPoseState();
    for (const listener of this.poseListeners) listener(state);
  }

  private emitAll(): void {
    this.emitCoarse();
    this.emitPose();
  }
}

/** The parts of a `KeyboardEvent` the tool reads. Kept plain so tests need no DOM. */
export interface FurnitureKeyEvent {
  readonly key: string;
  readonly shift?: boolean | undefined;
  /** Cmd on macOS, Ctrl elsewhere. Held means "not for this tool" — undo, redo. */
  readonly mod?: boolean | undefined;
}

export interface KeyOutcome {
  readonly handled: boolean;
  /** Present only when the keystroke produced ops (Enter). */
  readonly commit?: CommitResult | null | undefined;
}

/**
 * Only a DIGIT starts a numeric entry.
 *
 * This is the rule that keeps typing and tools from fighting. `m` is the
 * measure tool, `n` the window tool, `f` the furniture tool — if a bare letter
 * could start an entry, pressing M mid-placement would type "m" instead of
 * switching tools, and §12 promises the tool keys always work.
 */
function startsEntry(key: string): boolean {
  return key.length === 1 && key >= '0' && key <= '9';
}

/**
 * Once digits are down, unit marks may follow: `12'6"`, `3.8m`, `380cm`,
 * `12 ft`. Everything `parseLengthMm` accepts, and nothing else.
 */
function continuesEntry(key: string): boolean {
  return key.length === 1 && /[0-9.'"\- cfimnt]/i.test(key);
}

/**
 * The angle a fresh placement should start at, given the room it lands in.
 *
 * Furniture almost always goes against a wall, so the first guess is "back to
 * the nearest wall, front facing into the room" — which for a rectangular room
 * means facing away from the closest edge. Returns 0 when there is nothing to
 * infer from, and it is only ever a starting angle: R and free-rotate override
 * it immediately.
 */
export function suggestRotationDeg(pt: Pt, roomPolygon: readonly Pt[] | null): number {
  if (roomPolygon === null || roomPolygon.length < 3) return 0;

  let bestDistSq = Infinity;
  let bestDeg = 0;
  for (let i = 0; i < roomPolygon.length; i += 1) {
    const a = roomPolygon[i];
    const b = roomPolygon[(i + 1) % roomPolygon.length];
    if (a === undefined || b === undefined) continue;
    const abx = b.x - a.x;
    const aby = b.y - a.y;
    const lenSq = abx * abx + aby * aby;
    if (lenSq === 0) continue;
    const t = Math.max(0, Math.min(1, ((pt.x - a.x) * abx + (pt.y - a.y) * aby) / lenSq));
    const px = a.x + abx * t;
    const py = a.y + aby * t;
    const dSq = (pt.x - px) * (pt.x - px) + (pt.y - py) * (pt.y - py);
    if (dSq >= bestDistSq) continue;
    bestDistSq = dSq;
    // Face from the wall towards the point: that is the direction the item's
    // +Y (its front) should look along.
    const dx = pt.x - px;
    const dy = pt.y - py;
    // Item front is local +Y, i.e. 90° ahead of local +X, so subtract 90.
    bestDeg =
      dx === 0 && dy === 0 ? 0 : normaliseRotationDeg((Math.atan2(dy, dx) * 180) / Math.PI - 90);
  }
  // Snap the suggestion to the nearest right angle: an architect expects a
  // wardrobe to land square to the wall, not at 87°.
  return normaliseRotationDeg(Math.round(bestDeg / 90) * 90);
}
