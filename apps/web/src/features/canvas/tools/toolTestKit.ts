/**
 * toolTestKit.ts — TEST SUPPORT for the tool specs. Not product code.
 *
 * Nothing in `src/` may import this at runtime; it lives here (rather than in a
 * `__tests__` folder) for the same reason `packages/model/src/testing.ts` does —
 * so every spec in the directory builds its world the same way, and a change to
 * the {@link ToolContext} contract breaks one file instead of ten.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THE TOOLS ARE TESTABLE WITHOUT A CANVAS AT ALL
 * ────────────────────────────────────────────────────────────────────────────
 * A tool never touches React, three.js, a store or the network. It takes a
 * `ToolContext` (a value), receives `ToolPointerInput`/`ToolKeyInput` (values),
 * and returns a `ToolResponse` (a value). So the whole §12 state machine —
 * every phase transition, every op payload, every inline refusal — is
 * exercisable with three plain function calls and no renderer, which is exactly
 * what these helpers set up.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * DETERMINISM
 * ────────────────────────────────────────────────────────────────────────────
 * Ids come from {@link testIdFactory} rather than `newId`, so an asserted op
 * payload is byte-stable across runs. That is the same injection point the
 * copilot and the solver use, which is why `ToolContext.newId` exists.
 */

import {
  FIXTURE_IDS,
  applyGroup,
  fixedId,
  makeTwoRoomPlan,
  type ElementType,
  type Id,
  type Op,
  type OpByType,
  type OpType,
  type ProjectDoc,
  type Pt,
  type StoreyId,
  type WallKind,
} from '@garh/model';

import type { FurnitureItem } from '../../../lib/schemas';
// Type-only: `hitTest.ts` imports three at runtime and a tool spec must not.
import type { PickHit } from '../core/hitTest';
import { SNAP_COARSE_MM } from './constants';
import type {
  SetbackContext,
  Tool,
  ToolContext,
  ToolKeyInput,
  ToolPointerInput,
  ToolResponse,
  ToolSettings,
} from './types';
import { DEFAULT_TOOL_SETTINGS } from './useToolSettings';

export { FIXTURE_IDS, makeTwoRoomPlan };

// ---------------------------------------------------------------------------
// Ids
// ---------------------------------------------------------------------------

/**
 * A deterministic id minter: `wall_01J…T1`, `wall_01J…T2`, …
 *
 * Every context gets its own, so ids restart at 1 per test and an expected
 * payload can be written out in full.
 */
export function testIdFactory(): <T extends ElementType>(type: T) => Id<T> {
  let n = 0;
  return <T extends ElementType>(type: T): Id<T> => {
    n += 1;
    return fixedId(type, `T${String(n)}`);
  };
}

/** The id `testIdFactory` will mint for the `n`th call for `type` (1-based). */
export function nthId<T extends ElementType>(type: T, n: number): Id<T> {
  return fixedId(type, `T${String(n)}`);
}

// ---------------------------------------------------------------------------
// Input values
// ---------------------------------------------------------------------------

/** A pick that found nothing — the answer in every spec with no scene graph. */
export const EMPTY_HIT: PickHit = {
  kind: 'empty',
  id: null,
  storeyId: null,
  pointMm: null,
  elevationMm: 0,
  distanceWorld: Number.POSITIVE_INFINITY,
  object: null,
  instanceId: null,
};

/** A pick that names an element — for the paths that prefer the raycast. */
export function hitOn(kind: PickHit['kind'], id: string, storeyId: string): PickHit {
  return { ...EMPTY_HIT, kind, id, storeyId, distanceWorld: 1 };
}

export interface PointerOptions {
  readonly button?: number;
  readonly shiftKey?: boolean;
  readonly altKey?: boolean;
  readonly ctrlKey?: boolean;
  readonly metaKey?: boolean;
  readonly hit?: PickHit;
}

/**
 * A pointer event at integer millimetres.
 *
 * `pointMm` and `rawPointMm` are the same value: the tools read `rawPointMm`
 * and do their own snapping through `snapping.ts`, so handing them a
 * pre-snapped point would test the wrong thing.
 */
export function ptr(x: number, y: number, options: PointerOptions = {}): ToolPointerInput {
  const hit = options.hit ?? EMPTY_HIT;
  return {
    pointMm: { x, y },
    rawPointMm: { x, y },
    hit: () => hit,
    button: options.button ?? 0,
    shiftKey: options.shiftKey ?? false,
    altKey: options.altKey ?? false,
    ctrlKey: options.ctrlKey ?? false,
    metaKey: options.metaKey ?? false,
  };
}

/** A pointer event whose ray missed the ground plane (3D, above the horizon). */
export function ptrOffPlane(): ToolPointerInput {
  return {
    pointMm: null,
    rawPointMm: null,
    hit: () => EMPTY_HIT,
    button: 0,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
  };
}

export interface KeyOptions {
  readonly shiftKey?: boolean;
  readonly ctrlKey?: boolean;
  readonly metaKey?: boolean;
  readonly altKey?: boolean;
}

export function key(k: string, options: KeyOptions = {}): ToolKeyInput {
  return {
    key: k,
    shiftKey: options.shiftKey ?? false,
    ctrlKey: options.ctrlKey ?? false,
    metaKey: options.metaKey ?? false,
    altKey: options.altKey ?? false,
  };
}

/**
 * Type a string one character at a time, exactly as the capture-phase listener
 * in `useToolController` would deliver it. Returns every response so a spec can
 * assert that each keystroke was claimed by the tool (§12: typing a number
 * overrides the mouse).
 */
export function typeText(tool: Tool, ctx: ToolContext, text: string): ToolResponse[] {
  return [...text].map((ch) => tool.onKey(ctx, key(ch)));
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

export interface CtxOverrides {
  readonly doc?: ProjectDoc;
  readonly storeyId?: StoreyId | null;
  readonly snapModuleMm?: number;
  readonly mmPerPx?: number;
  readonly settings?: Partial<ToolSettings>;
  readonly setback?: SetbackContext | null;
  readonly furnitureCatalog?: ReadonlyMap<string, FurnitureItem>;
  readonly selectedIds?: readonly string[];
  readonly newId?: <T extends ElementType>(type: T) => Id<T>;
}

/**
 * The world a tool sees. Defaults to the two-room demo plan on the 115 mm
 * module at 1 mm/px — a deliberately tight zoom, so the 12 px object-snap
 * tolerance is 12 mm and a spec's coordinates mean what they say.
 */
export function makeCtx(overrides: CtxOverrides = {}): ToolContext {
  const doc = overrides.doc ?? makeTwoRoomPlan();
  return {
    doc,
    storeyId: overrides.storeyId === undefined ? FIXTURE_IDS.groundStorey : overrides.storeyId,
    snapModuleMm: overrides.snapModuleMm ?? SNAP_COARSE_MM,
    mmPerPx: overrides.mmPerPx ?? 1,
    unitsDisplay: doc.house.meta.unitsDisplay,
    settings: { ...DEFAULT_TOOL_SETTINGS, ...overrides.settings },
    setback: overrides.setback ?? null,
    furnitureCatalog: overrides.furnitureCatalog ?? new Map<string, FurnitureItem>(),
    selectedIds: overrides.selectedIds ?? [],
    newId: overrides.newId ?? testIdFactory(),
  };
}

/** Fold more ops onto a document — for specs that need a wall of their own. */
export function withOps(doc: ProjectDoc, ops: readonly Op[]): ProjectDoc {
  return applyGroup(doc, ops).model;
}

/** A wall on the ground storey, for the cases the fixture does not cover. */
export function addWall(
  doc: ProjectDoc,
  id: Id<'wall'>,
  a: Pt,
  b: Pt,
  thicknessMm = 115,
  kind: WallKind = 'internal',
): ProjectDoc {
  return withOps(doc, [
    {
      type: 'wall.add',
      payload: { id, storeyId: FIXTURE_IDS.groundStorey, a, b, thicknessMm, kind },
    },
  ]);
}

// ---------------------------------------------------------------------------
// Assertions
// ---------------------------------------------------------------------------

/**
 * Narrow an op to a type, failing loudly when it is not that type.
 *
 * `noUncheckedIndexedAccess` makes `ops[0]` an `Op | undefined`, and every spec
 * in here wants the narrowed payload rather than a chain of optional chaining
 * that would silently pass on an empty array.
 */
export function opOfType<T extends OpType>(op: Op | undefined, type: T): OpByType<T> {
  if (op === undefined) throw new Error(`expected an op of type ${type}, got none`);
  if (op.type !== type) throw new Error(`expected op type ${type}, got ${op.type}`);
  // `Extract<Op, { type: T }>` cannot be proven comparable while `T` is still
  // generic, so this is a two-step assertion guarded by the check above.
  return op as unknown as OpByType<T>;
}

/** Every op in the list, narrowed. Fails if any is the wrong type. */
export function opsOfType<T extends OpType>(ops: readonly Op[], type: T): OpByType<T>[] {
  return ops.map((op) => opOfType(op, type));
}

/**
 * Walk a preview's readouts by id. Returns null when absent, so a spec can
 * assert either presence or absence without a lookup helper of its own.
 */
export function readout(
  preview: { readonly readouts: readonly { readonly id: string; readonly value: string }[] },
  id: string,
): string | null {
  return preview.readouts.find((r) => r.id === id)?.value ?? null;
}

/** Chip ids on a preview, in order — the whole assertion for most chip tests. */
export function chipIds(preview: { readonly chips: readonly { readonly id: string }[] }): string[] {
  return preview.chips.map((c) => c.id);
}
