/**
 * types.ts — THE TOOL CONTRACT (§12).
 *
 * Every direct-manipulation tool in the 2D editor is a state machine with the
 * same five verbs and the same three guarantees. The verbs are
 * {@link Tool}; the guarantees are:
 *
 *   1. **Esc cancels.** Always, from any phase, without emitting an op.
 *   2. **Enter commits.** Whatever is previewable right now becomes ops.
 *   3. **Typing a number overrides the mouse.** While drawing, digits and
 *      length glyphs go to the tool's numeric entry, not to the keyboard map;
 *      the typed value replaces the pointer-derived one on the active field.
 *
 * §12 states all three as hard requirements, so they live in the shared type
 * rather than in each tool's good intentions: a tool that forgets `onKey` still
 * inherits the escape ladder from {@link BaseTool}, and the specs assert the
 * behaviour per tool.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE CONVERSION BOUNDARY (repeated in every module that has one)
 * ────────────────────────────────────────────────────────────────────────────
 * A tool NEVER sees screen pixels. `features/canvas/core/coords.ts` converts
 * pointer → integer millimetres before the event reaches here, and
 * {@link ToolPointerInput} carries the two forms a tool may want:
 *
 *   `pointMm`     already snapped to the active grid module — the value most
 *                 ops want, and the only one a lazy tool needs.
 *   `rawPointMm`  unsnapped (but still integer mm) — what `snapping.ts` needs
 *                 in order to decide whether an endpoint/midpoint/plot-edge
 *                 snap beats the grid.
 *
 * Both are integers by the time they arrive. Everything a tool computes on top
 * — lengths, offsets, projections — goes back through `snapMm`/`roundMm` from
 * `lib/units.ts` before it can become an op payload. There is no float in an
 * `Op` anywhere in this directory, and `canonicalJson` in the model core throws
 * if one ever appears, which is the backstop rather than the plan.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY TOOLS ARE PLAIN OBJECTS AND NOT REACT STATE
 * ────────────────────────────────────────────────────────────────────────────
 * §14 budgets a pointer move at well under 16 ms, and a React re-render per
 * `pointermove` blows that on a G+2 plan long before the renderer does. A tool
 * therefore holds its own mutable state, is kept in a ref by
 * `useToolController`, and publishes what the screen needs through
 * {@link ToolPreview} on `previewBus` — a mutable external store the overlay
 * reads once per animation frame. Nothing in the scene graph re-renders because
 * the mouse moved.
 */

import type {
  Direction4,
  ElementType,
  Id,
  Op,
  OpeningKind,
  OpeningSwing,
  Polygon,
  ProjectDoc,
  Pt,
  RailingKind,
  SizeMm,
  StairKind,
  StoreyId,
  UnitsDisplay,
  ValidationIssue,
  WallKind,
} from '@garh/model';

import type { ToolId } from '../../../lib/keymap';
import type { FurnitureItem } from '../../../lib/schemas';
// Type-only, and from the module rather than the `../core` barrel: the barrel
// is a runtime import of react-three-fiber, and these types must be usable in a
// spec that never mounts a canvas.
import type { PickHit } from '../core/hitTest';

export type { ToolId };

// ---------------------------------------------------------------------------
// Phases
// ---------------------------------------------------------------------------

/**
 * §12's `idle → drawing → preview → commit(op)`.
 *
 * `preview` is the phase where the shape is fully determined and only waiting
 * for confirmation — a placed-but-unconfirmed opening, a wall chain whose
 * length has been typed. `drawing` is the rubber-band phase where the pointer
 * still moves the geometry. Tools that have no confirmation step go
 * `idle → drawing → idle` and never enter `preview`; the phase exists so those
 * that do (openings, stairs, furniture) do not have to invent a private flag.
 */
export type ToolPhase = 'idle' | 'drawing' | 'preview';

// ---------------------------------------------------------------------------
// Settings — the tool options bar / inspector values
// ---------------------------------------------------------------------------

/** Parametric opening size. Comes from the inspector; the tool never guesses. */
export interface OpeningParams {
  readonly widthMm: number;
  readonly heightMm: number;
  readonly sillMm: number;
}

/**
 * Everything the tools read but do not own. Lives in `useToolSettings` (a
 * store), passed into every call so the machines stay pure enough to test
 * without React.
 */
export interface ToolSettings {
  /** 230 / 115 / 150 / 200 / custom — always integer mm. */
  readonly wallThicknessMm: number;
  readonly wallKind: WallKind;
  readonly wallLoadBearing: boolean;
  /** Ortho constraint on wall/balcony drawing. Shift inverts it live. */
  readonly ortho: boolean;

  readonly door: OpeningParams;
  readonly window: OpeningParams;
  readonly ventilator: OpeningParams;
  /** The N tool places a window or a ventilator; ⇧X toggles. */
  readonly windowVariant: 'window' | 'ventilator';
  readonly swing: OpeningSwing;

  readonly stairKind: StairKind;
  readonly stairDirection: Direction4;
  readonly stairWidthMm: number;
  /** Preferred riser; the flight solver moves off it to hit the storey height. */
  readonly stairPreferredRiserMm: number;

  readonly railingKind: RailingKind;
  readonly railingHeightMm: number;
  readonly balconySlabThicknessMm: number;

  /** Catalogue id to place. `null` until the architect picks one. */
  readonly furnitureCatalogId: string | null;
  readonly furnitureRotationDeg: number;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

/**
 * The buildable envelope for the active storey, when the page has one.
 *
 * Supplied by the integrator (the Plan page already resolves setbacks for the
 * plot editor) rather than recomputed here: the balcony tool must not become a
 * second, quietly divergent implementation of the setback table.
 */
export interface SetbackContext {
  /** Buildable envelope polygon in plot-local mm, or null when unknown. */
  readonly envelope: Polygon | null;
  /** Max permitted projection beyond the building line, or null. */
  readonly maxProjectionMm: number | null;
  /** Rule citation for the chip ("BBMP 2020 Table 5"), or null. */
  readonly cite: string | null;
}

/**
 * Everything a tool needs to know about the world, rebuilt per event by
 * `useToolController`. Read-only by construction: a tool that wants to change
 * the document returns ops (golden rule 1).
 */
export interface ToolContext {
  readonly doc: ProjectDoc;
  /** The storey being edited. `null` before the model has loaded. */
  readonly storeyId: StoreyId | null;
  /** 115 (module), 25 (fine) or 0 (off). Never negative. */
  readonly snapModuleMm: number;
  /** Current zoom, so snap and pick tolerances are constant in screen pixels. */
  readonly mmPerPx: number;
  readonly unitsDisplay: UnitsDisplay;
  readonly settings: ToolSettings;
  readonly setback: SetbackContext | null;
  /** Furniture catalogue by id. Empty until `/catalog/furniture` has loaded. */
  readonly furnitureCatalog: ReadonlyMap<string, FurnitureItem>;
  /** Currently selected element ids (the select tool's starting point). */
  readonly selectedIds: readonly string[];
  /**
   * Id minter. Injected so specs can assert exact op payloads without
   * `setUlidFactory` global state; defaults to the model core's `newId`.
   */
  readonly newId: <T extends ElementType>(type: T) => Id<T>;
}

// ---------------------------------------------------------------------------
// Input events
// ---------------------------------------------------------------------------

/**
 * A pointer event, already in model space.
 *
 * `hit` is a FUNCTION on purpose: `useCanvasControls` raycasts lazily and
 * memoises per event, so a tool that only needs `pointMm` (the wall tool
 * mid-drag) costs zero raycasts, and a tool that needs the pick (the door tool
 * hovering a wall) pays for exactly one.
 */
export interface ToolPointerInput {
  /** Snapped to the active grid module. Null when the ray missed the plane. */
  readonly pointMm: Pt | null;
  /** Unsnapped, still integer mm. Feeds `snapping.ts`. */
  readonly rawPointMm: Pt | null;
  readonly hit: () => PickHit;
  readonly button: number;
  readonly shiftKey: boolean;
  readonly altKey: boolean;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
}

/** A key press routed to the active tool. `key` is `KeyboardEvent.key`. */
export interface ToolKeyInput {
  readonly key: string;
  readonly shiftKey: boolean;
  readonly ctrlKey: boolean;
  readonly metaKey: boolean;
  readonly altKey: boolean;
}

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

/**
 * Ops a tool wants applied, as ONE undo group.
 *
 * `label` is the undo-toast copy from §15 ("Wall drawn", "Door added") —
 * sentence case, no trailing period, because the store renders it as
 * `Undo ${label.toLowerCase()}`.
 */
export interface ToolCommit {
  readonly ops: readonly Op[];
  readonly label: string;
  /** Select these after the ops apply — usually what was just created. */
  readonly selectIds?: readonly string[] | undefined;
}

/** What a tool wants done to the selection (the select tool only). */
export interface SelectionIntent {
  readonly mode: 'replace' | 'toggle' | 'add' | 'clear';
  readonly ids: readonly string[];
}

/** The return of every tool verb. All fields optional; `handled` is the tell. */
export interface ToolResponse {
  /** True when the tool consumed the event (the caller must not fall through). */
  readonly handled: boolean;
  /** Ops to dispatch as one group. */
  readonly commit?: ToolCommit | null | undefined;
  readonly selection?: SelectionIntent | null | undefined;
  /**
   * Tool options the tool wants changed — `X` flipping a door swing, `[`
   * cycling the stair type, a typed width becoming the new default.
   *
   * Tools do not own their settings (the options bar and the inspector edit the
   * same values), so they ASK. `useToolController` applies the patch to
   * `useToolSettings`; nothing else may write it from inside a tool.
   */
  readonly settingsPatch?: Partial<ToolSettings> | undefined;
  /** The preview changed — ask the canvas for a frame. */
  readonly redraw?: boolean | undefined;
  /** Switch back to the select tool after committing (placement tools do not). */
  readonly exitTool?: boolean | undefined;
}

/** Nothing happened. Shared instance: this is returned on most pointer moves. */
export const TOOL_RESPONSE_NONE: ToolResponse = { handled: false };

/** Helper for the common "I handled it and the screen changed" case. */
export function handled(extra: Omit<ToolResponse, 'handled'> = {}): ToolResponse {
  return { handled: true, redraw: true, ...extra };
}

// ---------------------------------------------------------------------------
// Preview
// ---------------------------------------------------------------------------

/** One wall-shaped thing in a preview: centreline plus the thickness to draw. */
export interface PreviewWall {
  readonly a: Pt;
  readonly b: Pt;
  readonly thicknessMm: number;
  readonly kind: WallKind;
  /** Length in mm — precomputed so the overlay never re-derives it. */
  readonly lengthMm: number;
  /** Bearing in integer degrees CCW from +X (east). */
  readonly angleDeg: number;
}

export interface MarqueeRectMm {
  readonly ax: number;
  readonly ay: number;
  readonly bx: number;
  readonly by: number;
}

/**
 * The geometry a preview draws. A discriminated union so the overlay renderer
 * switches once and knows nothing about which tool produced it — which is what
 * lets Phase 5 reuse the same previews in 3D without importing a tool.
 */
export type PreviewShape =
  | { readonly kind: 'none' }
  | {
      readonly kind: 'wall-chain';
      /** Segments already placed in this chain but NOT yet dispatched. */
      readonly segments: readonly PreviewWall[];
      /** The rubber-band segment following the pointer, if any. */
      readonly rubber: PreviewWall | null;
    }
  | {
      readonly kind: 'opening';
      readonly wallId: string | null;
      readonly openingKind: OpeningKind;
      readonly centreMm: Pt | null;
      readonly widthMm: number;
      readonly heightMm: number;
      readonly sillMm: number;
      readonly swing: OpeningSwing;
      readonly offsetMm: number;
      /** The opening's footprint along the host wall: [start, end] centreline. */
      readonly axis: readonly [Pt, Pt] | null;
    }
  | {
      readonly kind: 'stair';
      readonly footprint: Polygon;
      /** Tread lines, in travel order. */
      readonly treads: readonly (readonly [Pt, Pt])[];
      /** UP arrow: [tail, head]. */
      readonly arrow: readonly [Pt, Pt] | null;
      readonly risersCount: number;
      readonly riserMm: number;
      readonly treadMm: number;
    }
  | {
      readonly kind: 'polygon';
      readonly points: readonly Pt[];
      /** True once the ring closes (≥3 points and the pointer is on the first). */
      readonly closed: boolean;
      /** Rubber-band point following the pointer, if any. */
      readonly rubber: Pt | null;
    }
  | {
      readonly kind: 'furniture';
      readonly catalogId: string;
      readonly centreMm: Pt;
      readonly rotationDeg: number;
      readonly sizeMm: SizeMm;
    }
  | {
      readonly kind: 'measure';
      readonly points: readonly Pt[];
      readonly rubber: Pt | null;
      readonly segmentsMm: readonly number[];
      readonly totalMm: number;
    }
  | {
      readonly kind: 'transform';
      readonly targetIds: readonly string[];
      readonly ghosts: readonly PreviewWall[];
      readonly deltaMm: Pt;
    }
  | { readonly kind: 'marquee'; readonly rect: MarqueeRectMm };

/** A number the HUD shows while drawing: "Length 3,600 mm · 12'-0"". */
export interface Readout {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  /** The primary number — rendered larger. At most one per preview. */
  readonly emphasis?: boolean | undefined;
}

/**
 * A non-blocking chip (§15). Severity colour, one line of human text, a
 * citation on hover, and a fix hint. Tools raise these; they never refuse an
 * edit because of one.
 */
export interface ToolChip {
  readonly id: string;
  readonly severity: 'info' | 'warning' | 'error';
  readonly text: string;
  readonly cite: string | null;
  readonly fix: string | null;
}

/**
 * Why the tool will not commit right now — a hard stop, unlike a chip.
 *
 * This is where "refuse to place where validate would reject, and say why
 * inline rather than letting the server bounce it" lives: `issues` comes from
 * the real `validateOpAgainstDoc`, so the inline copy and the server's 422 copy
 * are the same string by construction.
 */
export interface ToolBlock {
  readonly message: string;
  readonly fix: string | null;
  readonly issues: readonly ValidationIssue[];
}

/** What a snap resolved to, for the overlay's snap marker. */
export interface SnapView {
  readonly kind: string;
  readonly label: string;
  readonly pointMm: Pt;
  readonly refId: string | null;
}

/** The live numeric-entry field, rendered inline next to the cursor. */
export interface NumericEntryView {
  readonly fieldId: string;
  readonly label: string;
  /** Exactly what the user has typed so far. */
  readonly buffer: string;
  /** Parsed value in the field's unit, or null while the text is incomplete. */
  readonly value: number | null;
  /** Formatted echo: `3,600 mm · 11'-10"`. Empty while unparseable. */
  readonly echo: string;
  /** Parse error copy, shown quietly under the field. Null while it parses. */
  readonly error: string | null;
  /** Other fields Tab cycles to, in order. */
  readonly fields: readonly { readonly id: string; readonly label: string }[];
}

/**
 * THE PUBLISHED PREVIEW. One object, replaced (never mutated) on every change,
 * so a consumer can cheaply compare by identity or by `version`.
 */
export interface ToolPreview {
  readonly toolId: ToolId;
  readonly phase: ToolPhase;
  readonly shape: PreviewShape;
  readonly snap: SnapView | null;
  readonly readouts: readonly Readout[];
  readonly entry: NumericEntryView | null;
  readonly chips: readonly ToolChip[];
  readonly blocked: ToolBlock | null;
  /** Where the tool thinks the cursor is, snapped. Drives the crosshair. */
  readonly cursorMm: Pt | null;
  /** Hint copy for the status bar: "Click to start the wall · Esc to cancel". */
  readonly hint: string;
  /** Monotonic; bumped whenever anything above changed. */
  readonly version: number;
}

// ---------------------------------------------------------------------------
// The Tool interface
// ---------------------------------------------------------------------------

/**
 * A drawing tool.
 *
 * Implementations are stateful objects, constructed once per activation by
 * `registry.createTool`. They are NOT React components and must not import
 * React: everything here has to be exercisable from a vitest spec with three
 * plain function calls, because the state machine is the part that breaks.
 */
export interface Tool {
  readonly id: ToolId;
  /** Current phase. Read-only to callers; the verbs move it. */
  readonly phase: ToolPhase;

  onPointerDown(ctx: ToolContext, event: ToolPointerInput): ToolResponse;
  onPointerMove(ctx: ToolContext, event: ToolPointerInput): ToolResponse;
  onPointerUp(ctx: ToolContext, event: ToolPointerInput): ToolResponse;
  /** Esc, Enter, Backspace, numeric entry and per-tool modifiers. */
  onKey(ctx: ToolContext, event: ToolKeyInput): ToolResponse;

  /** What to draw right now. Pure; called at most once per frame. */
  preview(ctx: ToolContext): ToolPreview;

  /**
   * Turn the current preview into ops, or null when there is nothing to
   * commit. Enter routes here, and so does the pointer path when a tool
   * commits on click. Committing does NOT reset the tool — `cancel()` does, and
   * tools that continue (the wall chain) rely on that separation.
   */
  commit(ctx: ToolContext): ToolCommit | null;

  /** Back to `idle`, discarding everything. Emits nothing, ever. */
  cancel(): void;

  /**
   * Does the tool want this key before the global keyboard map sees it?
   *
   * The §12 requirement "typing a number overrides the mouse" collides head-on
   * with the §12 keyboard map: `3` is the second-floor shortcut and `m` is the
   * measure tool, but `3.8m` is also a length. The rule this method encodes is
   * the resolution — **while a tool is mid-draw, number-ish keys belong to the
   * tool** — and `useToolController` consults it in a capture-phase listener
   * that runs before `useKeyboardMap`.
   */
  wantsKey(event: ToolKeyInput): boolean;
}
