/**
 * types.ts — the facade feature's vocabulary.
 *
 * Three groups of types live here:
 *
 *  1. **Kit definitions** (`FacadeKitDef` and friends) — the typed mirror of
 *     `fixtures/catalog/facade-kits.json`. The generator consumes these; the
 *     fixture JSON is what the API serves. `kits.fixture.test.ts` pins the two
 *     byte-for-byte so they cannot drift. (The API client's `facadeKitSchema`
 *     in `lib/schemas.ts` deliberately strips `components`/`rules` — the web
 *     app's generator runs client-side and owns the full shape.)
 *
 *  2. **Component param readers** — `facade.edit_component` (op 28) is an RFC
 *     7386 merge patch on a free-form `JsonObject`, so nothing about a
 *     component's params is guaranteed by the type system at render time. The
 *     readers here are the ONE defensive boundary: every consumer goes through
 *     `intParam` / `strParam` / `boolParam` and gets a documented fallback
 *     instead of an `undefined` that becomes `NaN` three frames later.
 *
 *  3. **The pick kind.** See {@link FACADE_PICK_KIND} below — it is the single
 *     deliberate compile error this module ships with until the integrator
 *     adds `'facade'` to the canvas core's `PICK_KINDS`.
 */

import type { JsonObject, JsonValue } from '@garh/model';

import type { PickKind } from '../core';

// ---------------------------------------------------------------------------
// Picking
// ---------------------------------------------------------------------------

/**
 * The pick kind every facade mesh registers under.
 *
 * INTEGRATOR CONTRACT — this line does not compile until
 * `features/canvas/core/constants.ts` (a shared file this feature must not
 * touch) gains:
 *
 *   - `'facade'` in `PICK_KINDS`;
 *   - `facade: 65` in `PICK_PRIORITY` — above `furniture` (60) and `wall`
 *     (40), below `opening` (70): a chajja hugs its host wall and must beat
 *     it under the cursor, but a window should still win over its own trim.
 *
 * The error is deliberate and single-point: a cast (`as unknown as PickKind`)
 * would compile today and deliver a pick kind the hit-tester's priority table
 * has never heard of — `PICK_PRIORITY[kind]` would be `undefined` and every
 * facade pick would lose every tie silently. Phase 4 shipped exactly one bug
 * of that shape (the unregistered furniture layer); this feature refuses to
 * ship the second.
 */
export const FACADE_PICK_KIND: PickKind = 'facade';

// ---------------------------------------------------------------------------
// Kit definitions (mirror of fixtures/catalog/facade-kits.json)
// ---------------------------------------------------------------------------

/** Window treatment: a proud flush band, or a shadow reveal set into the wall. */
export type WindowTrimStyle = 'flush-band' | 'recessed';

/** Chajja treatment: a visible flat slab, or a concealed lintel-depth drip. */
export type ChajjaStyle = 'flat' | 'hidden';

export type ParapetStyle = 'banded' | 'plain';

export type PorchStyle = 'cantilever' | 'flush';

/** Railing styles a kit may specify; op 28 may patch between them. */
export const RAILING_STYLES = ['ms-slim', 'glass', 'masonry'] as const;
export type RailingStyle = (typeof RAILING_STYLES)[number];

export interface KitWindowTrim {
  readonly style: WindowTrimStyle;
  readonly widthMm: number;
  /** Negative = recessed into the wall face. */
  readonly projectionMm: number;
}

export interface KitChajja {
  readonly style: ChajjaStyle;
  readonly projectionMm: number;
  readonly thicknessMm: number;
  /** The variants the seed may pick among (§8 "600|750 projection"). */
  readonly allowedProjectionsMm: readonly number[];
}

export interface KitParapetProfile {
  readonly style: ParapetStyle;
  readonly heightMm: number;
  readonly capThicknessMm: number;
}

export interface KitCladdingZones {
  /** Human-readable placement rule; `'none'` disables the component. */
  readonly rule: string;
  readonly materialId: string | null;
  readonly widthMm: number;
}

export interface KitPorch {
  readonly style: PorchStyle;
  readonly projectionMm: number;
  readonly thicknessMm: number;
}

export interface KitRailing {
  readonly style: RailingStyle;
  readonly heightMm: number;
  readonly materialId: string;
}

export interface KitColorway {
  readonly id: string;
  readonly name: string;
  /** Wall/base hex. */
  readonly base: string;
  /** Feature hex (cladding, porch soffit). */
  readonly accent: string;
  /** Trim/railing/cap hex. */
  readonly trim: string;
}

export interface KitRules {
  /** Below this external frontage the kit reads as clutter; the panel warns. */
  readonly minFacadeWidthMm: number;
  /** Which opening kinds get a chajja. */
  readonly chajjaOverOpenings: readonly string[];
  /** How the cladding bay is chosen (contemporary only). */
  readonly claddingBayPickedBy?: string;
  /** Reveal depth for recessed windows (modern-minimal only). */
  readonly recessDepthMm?: number;
}

export interface FacadeKitDef {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly components: {
    readonly windowTrim: KitWindowTrim;
    readonly chajja: KitChajja;
    readonly parapetProfile: KitParapetProfile;
    readonly claddingZones: KitCladdingZones;
    readonly porch: KitPorch;
    readonly railing: KitRailing;
  };
  readonly colorways: readonly KitColorway[];
  readonly rules: KitRules;
}

// ---------------------------------------------------------------------------
// Param readers — the defensive boundary around `component.params`
// ---------------------------------------------------------------------------

/**
 * Integer param or fallback. Op validation (`checkJsonIntegral`) guarantees any
 * number in params is a safe integer, but a patch may have *deleted* the key
 * (RFC 7386 `null`), so absence is a legal state, not a bug.
 */
export function intParam(params: JsonObject, key: string, fallback: number): number {
  const v: JsonValue | undefined = params[key];
  return typeof v === 'number' && Number.isSafeInteger(v) ? v : fallback;
}

/** String param or fallback. */
export function strParam(params: JsonObject, key: string, fallback: string): string {
  const v: JsonValue | undefined = params[key];
  return typeof v === 'string' ? v : fallback;
}

/** String param that must be one of `allowed`, else fallback. */
export function enumParam<T extends string>(
  params: JsonObject,
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const v: JsonValue | undefined = params[key];
  return typeof v === 'string' && (allowed as readonly string[]).includes(v)
    ? (v as T)
    : fallback;
}
