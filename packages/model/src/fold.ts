/**
 * fold.ts — the op engine: `fold`, `replay`, `applyGroup`, undo/redo,
 * `canonicalJson` and `stateHash`.
 *
 * `fold(model, op)` is PURE and DETERMINISTIC:
 *   - it never reads the clock, never generates a random id, never mutates its
 *     input (creation ops carry their ids; see ops.ts),
 *   - the same op log always folds to the same document and therefore the same
 *     `stateHash`, in TypeScript and in the Python mirror alike,
 *   - it returns an INVERSE op list, so undo is "apply the inverse", not
 *     "restore a snapshot".
 *
 * ============================================================================
 * stateHash — CROSS-LANGUAGE CONTRACT (must match apps/api/garh_model byte for
 * byte, because `design_versions.snapshot_hash` is compared across languages).
 *
 *   stateHash(v) = lowercase_hex( sha256( utf8( canonicalJson(v) ) ) )
 *
 * canonicalJson rules, exactly:
 *   1. `null` -> `null`; `true`/`false` -> `true`/`false`.
 *   2. Numbers MUST be safe integers (`Number.isSafeInteger`). Anything else —
 *      float, NaN, Infinity, 1e21 — throws `CanonicalJsonError`. There are no
 *      floats in this document by construction (geometry is integer mm), and
 *      that is what makes the hash portable. `-0` serialises as `0`.
 *      Integers are written in plain decimal: no `+`, no exponent, no padding.
 *   3. Strings are quoted with `"` and escaped MINIMALLY:
 *        `\` -> `\\`,  `"` -> `\"`,
 *        U+0008 -> `\b`, U+0009 -> `\t`, U+000A -> `\n`, U+000C -> `\f`,
 *        U+000D -> `\r`, any other code point < 0x20 -> `\u00xx` with LOWERCASE
 *        hex. Everything else (including all non-ASCII) is emitted literally as
 *        UTF-8 — no `\uXXXX` escaping, no escaping of `/`, U+2028 or U+2029.
 *        A lone surrogate throws.
 *   4. Arrays keep their order. An `undefined` element throws.
 *   5. Object keys are sorted ASCENDING BY UNICODE CODE POINT (not UTF-16 code
 *      unit — see `compareCodePoints`). Keys whose value is `undefined` are
 *      OMITTED entirely (this is how optional TS fields disappear).
 *   6. No whitespace anywhere: separators are exactly `,` and `:`.
 *   7. Any other JS value (function, symbol, bigint, Date, Map, Set, class
 *      instance) throws.
 *
 * The Python mirror can implement rule 1-7 as:
 *   json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
 *              allow_nan=False)
 * after asserting every number is an `int` (never a `float`, never a `bool`
 * masquerading as one) and dropping keys whose value is `None`-by-omission.
 *
 * ELEMENT ORDER is part of the canonical form: `finalize()` sorts every element
 * array by id ascending (byte compare) before the document is returned, so two
 * documents with the same content hash the same regardless of insertion order.
 * `storeys` and `levels.fflPerStoreyMm` keep their semantic order (ground = 0).
 * ============================================================================
 */

import { polygonAreaMm2, pointAlongSeg, rectPolygon, segmentLengthMm } from './geometry';
import type { Polygon, Pt } from './geometry';
import { derivedId } from './ids';
import type { GroupId, RoomId, StoreyId } from './ids';
import { DEFAULTS, SCHEMA_VERSION, defaultLevelData, emptyProjectDoc } from './model';
import type {
  Annotation,
  Balcony,
  BriefDoc,
  Column,
  Direction4,
  FacadeComponent,
  FacadeModel,
  FurnitureInstance,
  HouseModel,
  JsonObject,
  JsonValue,
  LevelData,
  Levels,
  MaterialAssignment,
  ModelMeta,
  Opening,
  PlotDoc,
  ProjectDoc,
  Road,
  Room,
  Slab,
  Stair,
  Storey,
  SurfaceGroupRef,
  Wall,
} from './model';
import type { Op, OpType } from './ops';
import { detectRooms } from './rooms';
import { sha256Utf8 } from './sha256';
import { OpRejectedError, validateModel, validateOpAgainstDoc, validateOpShape } from './validate';
import type { ValidationIssue } from './validate';

// ---------------------------------------------------------------------------
// Canonical JSON + state hash
// ---------------------------------------------------------------------------

/** Version tag of the canonicalisation rules. Bump ⇒ every stored hash changes. */
export const CANONICAL_JSON_SPEC = 'garh-canonical-json/v1';

/** Human-readable name of the hash algorithm, for logs and DB comments. */
export const STATE_HASH_ALGORITHM = `sha256(${CANONICAL_JSON_SPEC})`;

export class CanonicalJsonError extends Error {
  readonly code = 'CANONICAL_JSON_INVALID';
  readonly path: string;
  constructor(message: string, path: string) {
    super(`${message} (at ${path === '' ? '$' : path})`);
    this.name = 'CanonicalJsonError';
    this.path = path;
  }
}

/** Compare two strings by Unicode CODE POINT, not UTF-16 code unit. */
export function compareCodePoints(a: string, b: string): number {
  const ca = Array.from(a);
  const cb = Array.from(b);
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) {
    const pa = ca[i]!.codePointAt(0) ?? 0;
    const pb = cb[i]!.codePointAt(0) ?? 0;
    if (pa !== pb) return pa < pb ? -1 : 1;
  }
  if (ca.length !== cb.length) return ca.length < cb.length ? -1 : 1;
  return 0;
}

const ESCAPES: Record<number, string> = {
  0x08: '\\b',
  0x09: '\\t',
  0x0a: '\\n',
  0x0c: '\\f',
  0x0d: '\\r',
  0x22: '\\"',
  0x5c: '\\\\',
};

function canonicalString(s: string, path: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const code = s.charCodeAt(i);
    const esc = ESCAPES[code];
    if (esc !== undefined) {
      out += esc;
      continue;
    }
    if (code < 0x20) {
      out += `\\u${code.toString(16).padStart(4, '0')}`;
      continue;
    }
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (next < 0xdc00 || next > 0xdfff) {
        throw new CanonicalJsonError('Lone high surrogate in string', path);
      }
      out += s[i]! + s[i + 1]!;
      i++;
      continue;
    }
    if (code >= 0xdc00 && code <= 0xdfff) {
      throw new CanonicalJsonError('Lone low surrogate in string', path);
    }
    out += s[i];
  }
  return `${out}"`;
}

function canonicalWrite(value: unknown, path: string): string {
  if (value === null) return 'null';
  switch (typeof value) {
    case 'boolean':
      return value ? 'true' : 'false';
    case 'number': {
      if (!Number.isSafeInteger(value)) {
        throw new CanonicalJsonError(
          `Only safe integers may be serialised (got ${String(value)}). Geometry is integer mm; ` +
            'brief/override JSON must use integers too.',
          path,
        );
      }
      return Object.is(value, -0) ? '0' : String(value);
    }
    case 'string':
      return canonicalString(value, path);
    case 'undefined':
      throw new CanonicalJsonError('undefined cannot be serialised here', path);
    case 'object': {
      if (Array.isArray(value)) {
        const parts: string[] = [];
        for (let i = 0; i < value.length; i++) {
          if (value[i] === undefined) {
            throw new CanonicalJsonError('undefined array element', `${path}[${String(i)}]`);
          }
          parts.push(canonicalWrite(value[i], `${path}[${String(i)}]`));
        }
        return `[${parts.join(',')}]`;
      }
      const obj = value as Record<string, unknown>;
      const proto = Object.getPrototypeOf(obj) as unknown;
      if (proto !== Object.prototype && proto !== null) {
        throw new CanonicalJsonError(
          `Only plain objects may be serialised (got ${obj.constructor?.name ?? 'unknown'})`,
          path,
        );
      }
      const keys = Object.keys(obj)
        .filter((k) => obj[k] !== undefined)
        .sort(compareCodePoints);
      const parts = keys.map(
        (k) => `${canonicalString(k, path)}:${canonicalWrite(obj[k], `${path}.${k}`)}`,
      );
      return `{${parts.join(',')}}`;
    }
    default:
      throw new CanonicalJsonError(`Cannot serialise a ${typeof value}`, path);
  }
}

/** Canonical JSON per the rules in this file's header. */
export function canonicalJson(value: unknown): string {
  return canonicalWrite(value, '');
}

/** sha256 of the canonical JSON — 64 lowercase hex chars. */
export function stateHash(value: unknown): string {
  return sha256Utf8(canonicalJson(value));
}

/** The document hash stored in `design_versions.snapshot_hash`. */
export function docHash(doc: ProjectDoc): string {
  return stateHash(doc);
}

// ---------------------------------------------------------------------------
// RFC 7386 JSON merge patch (brief.update, facade.edit_component)
// ---------------------------------------------------------------------------

function isJsonObject(v: unknown): v is JsonObject {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** RFC 7386: `null` deletes a key, objects merge recursively, else replace. */
export function applyMergePatch(target: JsonObject, patch: JsonObject): JsonObject {
  const out: JsonObject = { ...target };
  for (const key of Object.keys(patch)) {
    const pv = patch[key];
    if (pv === undefined) continue;
    if (pv === null) {
      delete out[key];
      continue;
    }
    const tv = out[key];
    if (isJsonObject(pv)) {
      out[key] = applyMergePatch(isJsonObject(tv) ? tv : {}, pv);
    } else {
      out[key] = pv;
    }
  }
  return out;
}

/** The patch that undoes `patch` when applied to `applyMergePatch(target, patch)`. */
export function invertMergePatch(target: JsonObject, patch: JsonObject): JsonObject {
  const out: JsonObject = {};
  for (const key of Object.keys(patch)) {
    const pv = patch[key];
    const had = Object.prototype.hasOwnProperty.call(target, key);
    const tv = target[key];
    if (pv === null) {
      out[key] = had ? (tv as JsonValue) : null;
    } else if (isJsonObject(pv) && isJsonObject(tv)) {
      out[key] = invertMergePatch(tv, pv);
    } else {
      out[key] = had ? (tv as JsonValue) : null;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Draft (a mutable working copy of a ProjectDoc)
// ---------------------------------------------------------------------------

interface Draft {
  schemaVersion: number;
  plot: PlotDoc;
  brief: BriefDoc;
  annotations: Annotation[];
  storeys: Storey[];
  walls: Wall[];
  openings: Opening[];
  rooms: Room[];
  stairs: Stair[];
  slabs: Slab[];
  columns: Column[];
  furniture: FurnitureInstance[];
  facade: FacadeModel;
  materials: MaterialAssignment[];
  levels: Levels;
  balconies: Balcony[];
  meta: ModelMeta;
  /** Storeys whose walls changed ⇒ rooms and slabs must be recomputed. */
  dirtyStoreys: Set<string>;
  /**
   * Storeys the op touched at all. The post-apply invariant check is scoped to
   * these so the quadratic wall-overlap scan stays inside the <10ms op budget.
   * Empty ⇒ validate the whole document (plot/levels/brief ops).
   */
  touchedStoreys: Set<string>;
  /** True when FFLs should be re-derived from plinth + storey heights. */
  deriveLevels: boolean;
}

function toDraft(doc: ProjectDoc): Draft {
  const h = doc.house;
  return {
    schemaVersion: doc.schemaVersion,
    plot: doc.plot,
    brief: doc.brief,
    annotations: doc.annotations.slice(),
    storeys: h.storeys.slice(),
    walls: h.walls.slice(),
    openings: h.openings.slice(),
    rooms: h.rooms.slice(),
    stairs: h.stairs.slice(),
    slabs: h.slabs.slice(),
    columns: h.columns.slice(),
    furniture: h.furniture.slice(),
    facade: h.facade,
    materials: h.materials.slice(),
    levels: h.levels,
    balconies: h.balconies.slice(),
    meta: h.meta,
    dirtyStoreys: new Set<string>(),
    touchedStoreys: new Set<string>(),
    deriveLevels: false,
  };
}

function byId(a: { id: string }, b: { id: string }): number {
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** Derive FFL per storey from plinth + storey heights (ground FFL = plinth). */
function deriveFflPerStorey(storeys: readonly Storey[], plinthMm: number): number[] {
  const out: number[] = [];
  let ffl = plinthMm;
  for (const s of storeys) {
    out.push(ffl);
    ffl += s.heightMm;
  }
  return out;
}

/**
 * Footprint of a stair, used for slab cut-outs.
 *
 * EXACT for `straight`. For `dogleg` / `L` / `U` this is the BOUNDING RECTANGLE
 * of the flights plus landing — good enough for a slab void and for the "UP 15R"
 * arrow block, and deliberately not pretending to be the true outline.
 */
export function stairFootprintPolygon(stair: Stair): Polygon {
  const goingOf = (risers: number): number => Math.max(1, risers - 1) * stair.treadMm;
  const extent = ((): { depthMm: number; widthMm: number } => {
    if (stair.kind === 'straight') {
      return { depthMm: goingOf(stair.risersCount), widthMm: stair.widthMm };
    }
    // eslint-disable-next-line no-restricted-properties -- splits a riser COUNT across two flights; counts are not lengths, no mm rounding involved
    const perFlight = Math.ceil(stair.risersCount / 2);
    const depthMm = goingOf(perFlight) + (stair.landing?.depthMm ?? stair.widthMm);
    if (stair.kind === 'L') {
      return { depthMm, widthMm: stair.widthMm + (stair.landing?.widthMm ?? stair.widthMm) };
    }
    // dogleg and U: two parallel flights either side of the landing
    return { depthMm, widthMm: stair.landing?.widthMm ?? 2 * stair.widthMm + 100 };
  })();

  // forward = direction of travel, right = 90 degrees clockwise from forward
  const VECTORS: Record<Direction4, { fx: number; fy: number; rx: number; ry: number }> = {
    N: { fx: 0, fy: 1, rx: 1, ry: 0 },
    E: { fx: 1, fy: 0, rx: 0, ry: -1 },
    S: { fx: 0, fy: -1, rx: -1, ry: 0 },
    W: { fx: -1, fy: 0, rx: 0, ry: 1 },
  };
  const v = VECTORS[stair.direction];
  const { x, y } = stair.origin;
  const xs = [
    x,
    x + v.rx * extent.widthMm,
    x + v.rx * extent.widthMm + v.fx * extent.depthMm,
    x + v.fx * extent.depthMm,
  ];
  const ys = [
    y,
    y + v.ry * extent.widthMm,
    y + v.ry * extent.widthMm + v.fy * extent.depthMm,
    y + v.fy * extent.depthMm,
  ];
  return rectPolygon(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys));
}

/** Rebuild the derived rooms and slabs of every dirty storey. */
function recomputeDerived(draft: Draft): void {
  if (draft.dirtyStoreys.size === 0) return;
  for (const storeyId of draft.dirtyStoreys) {
    const storey = draft.storeys.find((s) => s.id === storeyId);
    if (!storey) continue;

    const otherRooms = draft.rooms.filter((r) => r.storeyId !== storeyId);
    const takenIds = new Set<string>([
      ...draft.storeys.map((s) => s.id),
      ...draft.walls.map((w) => w.id),
      ...draft.openings.map((o) => o.id),
      ...otherRooms.map((r) => r.id),
      ...draft.stairs.map((s) => s.id),
      ...draft.columns.map((c) => c.id),
      ...draft.furniture.map((f) => f.id),
      ...draft.balconies.map((b) => b.id),
      ...draft.materials.map((m) => m.id),
      ...draft.annotations.map((a) => a.id),
    ]);

    const detection = detectRooms(
      draft.walls,
      storeyId,
      draft.rooms.filter((r) => r.storeyId === storeyId),
      takenIds,
    );
    draft.rooms = [...otherRooms, ...detection.rooms];

    // --- slab: outline of this storey's walls, with stair wells from below
    const storeyIdx = draft.storeys.findIndex((s) => s.id === storeyId);
    const below = storeyIdx > 0 ? draft.storeys[storeyIdx - 1] : undefined;
    const cutouts: Polygon[] = below
      ? draft.stairs.filter((s) => s.storeyId === below.id).map((s) => stairFootprintPolygon(s))
      : [];
    draft.slabs = draft.slabs.filter((s) => s.storeyId !== storeyId);
    if (detection.outline && detection.outline.length >= 3) {
      draft.slabs.push({
        id: derivedId('slab', `${storeyId}|floor`),
        storeyId,
        kind: 'floor',
        polygon: detection.outline,
        thicknessMm: storey.level.slabThicknessMm,
        cutouts,
      });
    }
  }
}

function finalize(draft: Draft): ProjectDoc {
  if (draft.deriveLevels) {
    draft.levels = {
      ...draft.levels,
      fflPerStoreyMm: deriveFflPerStorey(draft.storeys, draft.levels.plinthMm),
    };
    const ffls = draft.levels.fflPerStoreyMm;
    draft.storeys = draft.storeys.map((s, i) => ({
      ...s,
      level: { ...s.level, fflMm: ffls[i] ?? s.level.fflMm },
    }));
  }
  recomputeDerived(draft);

  const house: HouseModel = {
    schemaVersion: draft.schemaVersion,
    storeys: draft.storeys,
    walls: draft.walls.slice().sort(byId),
    openings: draft.openings.slice().sort(byId),
    rooms: draft.rooms.slice().sort(byId),
    stairs: draft.stairs.slice().sort(byId),
    slabs: draft.slabs.slice().sort(byId),
    columns: draft.columns.slice().sort(byId),
    furniture: draft.furniture.slice().sort(byId),
    facade: {
      ...draft.facade,
      components: draft.facade.components.slice().sort(byId),
    },
    materials: draft.materials.slice().sort(byId),
    levels: draft.levels,
    balconies: draft.balconies.slice().sort(byId),
    meta: draft.meta,
  };
  return {
    schemaVersion: draft.schemaVersion,
    plot: draft.plot,
    brief: draft.brief,
    house,
    annotations: draft.annotations.slice().sort(byId),
  };
}

// ---------------------------------------------------------------------------
// fold
// ---------------------------------------------------------------------------

export interface FoldResult {
  /** The next document. The input is never mutated. */
  readonly model: ProjectDoc;
  /**
   * Ops that, applied IN ORDER to `model`, restore the input document.
   * Usually one op; destructive ops return several (e.g. deleting a wall
   * returns `wall.add` followed by an `opening.add` per hosted opening).
   */
  readonly inverse: readonly Op[];
}

export interface FoldOptions {
  /** Skip inverse computation (used for dry runs). Default true. */
  readonly computeInverse?: boolean;
  /** Skip the post-apply invariant check (used internally). Default true. */
  readonly validateResult?: boolean;
}

/** Non-throwing fold outcome, for the copilot dry-run loop (§10). */
export type FoldOutcome =
  | { readonly ok: true; readonly model: ProjectDoc; readonly inverse: readonly Op[] }
  | { readonly ok: false; readonly issues: readonly ValidationIssue[] };

/**
 * Apply one op. Pure: returns a new document plus the inverse ops.
 * @throws {OpRejectedError} when the op is invalid or inapplicable.
 */
export function fold(model: ProjectDoc, op: Op, options: FoldOptions = {}): FoldResult {
  const shapeIssues = validateOpShape(op);
  if (shapeIssues.length > 0) throw new OpRejectedError(op.type, shapeIssues);
  const docIssues = validateOpAgainstDoc(model, op);
  if (docIssues.length > 0) throw new OpRejectedError(op.type, docIssues);

  // A solver option is its own atomic group: delegate so that every expanded op
  // is validated against the intermediate state and the inverse is the reversed
  // concatenation of the inner inverses.
  if (op.type === 'solver.apply_option') {
    const group = applyGroup(model, op.payload.ops, op.groupId as GroupId | undefined);
    return { model: group.model, inverse: group.inverse };
  }

  const computeInverse = options.computeInverse ?? true;
  const draft = toDraft(model);
  const inverse: Op[] = [];

  applyOp(draft, op, inverse, computeInverse);

  const next = finalize(draft);

  if (options.validateResult !== false) {
    const touched = draft.touchedStoreys.size > 0 ? Array.from(draft.touchedStoreys) : undefined;
    // Conditional spread: exactOptionalPropertyTypes forbids an explicit
    // `storeyIds: undefined` — absent and undefined are different things here.
    const issues = validateModel(next, {
      includeWarnings: false,
      ...(touched === undefined ? {} : { storeyIds: touched }),
    });
    if (issues.length > 0) throw new OpRejectedError(op.type, issues);
  }

  const finalInverse =
    computeInverse && DESTRUCTIVE_OPS.has(op.type)
      ? withRoomMetadataRestore(model, next, inverse)
      : inverse;

  return { model: next, inverse: finalInverse };
}

/** `fold` without exceptions — the shape the copilot validation loop wants. */
export function tryFold(model: ProjectDoc, op: Op, options: FoldOptions = {}): FoldOutcome {
  try {
    const r = fold(model, op, options);
    return { ok: true, model: r.model, inverse: r.inverse };
  } catch (e) {
    if (e instanceof OpRejectedError) return { ok: false, issues: e.issues };
    throw e;
  }
}

/**
 * Ops that can destroy a room (and therefore its type/name/lock metadata).
 * For these, the inverse is topped up with `room.assign` / `room.set_target`
 * ops so undo restores the metadata of every room whose id comes back — see
 * `withRoomMetadataRestore`.
 */
const DESTRUCTIVE_OPS: ReadonlySet<OpType> = new Set<OpType>([
  'wall.delete',
  'wall.move',
  'wall.split',
  'wall.set_thickness',
  'storey.remove',
]);

/**
 * Rooms are derived, so an inverse that restores walls also restores room
 * GEOMETRY — but a room destroyed by a merge comes back as a fresh room with no
 * type or name. Fix that honestly: dry-run the inverse, see which room ids
 * actually reappear, and append `room.assign` / `room.set_target` for exactly
 * those. Ops are only emitted for rooms proven to exist after the undo, so the
 * inverse group can never fail to apply.
 *
 * Limitation: if a room id does NOT reappear (its polygon was re-derived under a
 * different id), its name is lost. That is visible in the returned inverse — no
 * op is emitted for it — rather than silently mis-attached.
 */
function withRoomMetadataRestore(before: ProjectDoc, after: ProjectDoc, inverseOps: Op[]): Op[] {
  if (inverseOps.length === 0) return inverseOps;
  let dry: ProjectDoc;
  try {
    dry = after;
    for (const op of inverseOps) {
      dry = fold(dry, op, { computeInverse: false }).model;
    }
  } catch {
    return inverseOps; // cannot predict; leave the geometric inverse alone
  }
  const restoredById = new Map(dry.house.rooms.map((r) => [r.id, r]));
  const extra: Op[] = [];
  for (const room of before.house.rooms) {
    const restored = restoredById.get(room.id);
    if (!restored) continue;
    const tagsDiffer =
      restored.tags.length !== room.tags.length || restored.tags.some((t, i) => t !== room.tags[i]);
    if (
      restored.type !== room.type ||
      restored.name !== room.name ||
      restored.locked !== room.locked ||
      tagsDiffer
    ) {
      extra.push({
        type: 'room.assign',
        payload: {
          roomId: room.id,
          type: room.type,
          name: room.name,
          tags: room.tags,
          locked: room.locked,
        },
      });
    }
    if (restored.targetAreaMm2 !== room.targetAreaMm2 || restored.mustFace !== room.mustFace) {
      extra.push({
        type: 'room.set_target',
        payload: {
          roomId: room.id,
          targetAreaMm2: room.targetAreaMm2,
          mustFace: room.mustFace,
        },
      });
    }
  }
  return extra.length === 0 ? inverseOps : [...inverseOps, ...extra];
}

// ---------------------------------------------------------------------------
// The op switch
// ---------------------------------------------------------------------------

function applyOp(draft: Draft, op: Op, inverse: Op[], wantInverse: boolean): void {
  const push = (inv: Op): void => {
    if (wantInverse) inverse.push(inv);
  };

  switch (op.type) {
    // ---------------------------------------------------------------- plot
    case 'plot.set_boundary': {
      const prev = draft.plot;
      // Roads reference edge indices, so a boundary with fewer edges drops the
      // roads that no longer have an edge. The inverse restores the boundary
      // AND re-adds those roads, so undo is lossless.
      const kept = prev.roads.filter((r) => r.edgeIndex < op.payload.polygon.length);
      const dropped = prev.roads.filter((r) => r.edgeIndex >= op.payload.polygon.length);
      push({
        type: 'plot.set_boundary',
        payload: { polygon: prev.boundary.slice(), source: prev.source },
      });
      for (const road of dropped) {
        push({
          type: 'plot.set_road',
          payload: { edgeIndex: road.edgeIndex, widthMm: road.widthMm, name: road.name },
        });
      }
      draft.plot = {
        ...prev,
        boundary: op.payload.polygon.slice(),
        source: op.payload.source ?? prev.source,
        roads: kept,
      };
      break;
    }
    case 'plot.set_north': {
      push({ type: 'plot.set_north', payload: { deg: draft.plot.northDeg } });
      draft.plot = { ...draft.plot, northDeg: op.payload.deg };
      break;
    }
    case 'plot.set_road': {
      const prev = draft.plot.roads.find((r) => r.edgeIndex === op.payload.edgeIndex);
      push({
        type: 'plot.set_road',
        payload: {
          edgeIndex: op.payload.edgeIndex,
          widthMm: prev?.widthMm ?? null,
          name: prev?.name ?? null,
        },
      });
      const roads: Road[] = draft.plot.roads.filter((r) => r.edgeIndex !== op.payload.edgeIndex);
      if (op.payload.widthMm !== null) {
        roads.push({
          edgeIndex: op.payload.edgeIndex,
          widthMm: op.payload.widthMm,
          name: op.payload.name ?? null,
        });
      }
      roads.sort((a, b) => a.edgeIndex - b.edgeIndex);
      draft.plot = { ...draft.plot, roads };
      break;
    }
    case 'plot.set_reg_profile': {
      const prev = draft.plot.regProfile;
      push({
        type: 'plot.set_reg_profile',
        payload: { cityPack: prev.cityPack, overrides: { ...prev.overrides } },
      });
      draft.plot = {
        ...draft.plot,
        regProfile: { cityPack: op.payload.cityPack, overrides: { ...op.payload.overrides } },
      };
      draft.meta = { ...draft.meta, regProfileRef: op.payload.cityPack };
      break;
    }

    // --------------------------------------------------------------- brief
    case 'brief.update': {
      const prev = draft.brief;
      const invPatch = invertMergePatch(prev.data, op.payload.patch);
      push({
        type: 'brief.update',
        payload: {
          patch: invPatch,
          vastuMode: prev.vastuMode,
          completeness: prev.completeness,
        },
      });
      draft.brief = {
        data: applyMergePatch(prev.data, op.payload.patch),
        vastuMode: op.payload.vastuMode ?? prev.vastuMode,
        completeness: op.payload.completeness ?? prev.completeness,
      };
      break;
    }

    // -------------------------------------------------------------- storeys
    case 'storey.add': {
      const index = Math.min(op.payload.index, draft.storeys.length);
      const storey: Storey = {
        id: op.payload.id,
        name: op.payload.name ?? defaultStoreyName(index),
        // normaliseLevel, not a bare `?? defaultLevelData(0)`: a wire payload may
        // legally carry {fflMm, slabThicknessMm} only (validateOpShape allows the
        // optional sub-keys to be absent). Storing that object verbatim leaves
        // sillDefaultMm/lintelDefaultMm *absent*, canonicalJson omits absent keys,
        // and the document then hashes differently from the Python mirror — which
        // writes explicit nulls — and violates common.schema.json $defs.LevelData,
        // whose `required` lists all four. See CROSS-LANGUAGE CONTRACT below.
        level: normaliseLevel(op.payload.level),
        heightMm: op.payload.heightMm,
      };
      draft.storeys.splice(index, 0, storey);
      touch(draft, storey.id);
      draft.deriveLevels = true;
      push({ type: 'storey.remove', payload: { index } });
      break;
    }
    case 'storey.remove': {
      const index = op.payload.index;
      const storey = draft.storeys[index];
      if (!storey) break;
      if (wantInverse) {
        inverse.push({
          type: 'storey.add',
          payload: {
            id: storey.id,
            index,
            name: storey.name,
            heightMm: storey.heightMm,
            level: storey.level,
          },
        });
        for (const w of draft.walls.filter((x) => x.storeyId === storey.id)) {
          inverse.push({
            type: 'wall.add',
            payload: {
              id: w.id,
              storeyId: w.storeyId,
              a: w.a,
              b: w.b,
              thicknessMm: w.thicknessMm,
              kind: w.kind,
              loadBearing: w.loadBearing,
            },
          });
        }
        const wallIds = new Set(
          draft.walls.filter((x) => x.storeyId === storey.id).map((x) => x.id),
        );
        for (const o of draft.openings.filter((x) => wallIds.has(x.wallId))) {
          inverse.push({ type: 'opening.add', payload: openingAddPayload(o) });
        }
        for (const s of draft.stairs.filter((x) => x.storeyId === storey.id)) {
          inverse.push({ type: 'stair.add', payload: stairAddPayload(s) });
        }
        for (const c of draft.columns.filter((x) => x.storeyId === storey.id)) {
          inverse.push({
            type: 'column.set',
            payload: { action: 'add', id: c.id, storeyId: c.storeyId, pt: c.pt, sizeMm: c.sizeMm },
          });
        }
        for (const f of draft.furniture.filter((x) => x.storeyId === storey.id)) {
          inverse.push({
            type: 'furniture.set',
            payload: {
              action: 'place',
              id: f.id,
              storeyId: f.storeyId,
              catalogId: f.catalogId,
              pt: f.pt,
              rotationDeg: f.rotationDeg,
            },
          });
        }
        for (const b of draft.balconies.filter((x) => x.storeyId === storey.id)) {
          inverse.push({
            type: 'balcony.set',
            payload: {
              action: 'add',
              id: b.id,
              storeyId: b.storeyId,
              polygon: b.polygon,
              railingKind: b.railingKind,
              railingHeightMm: b.railingHeightMm,
              projectionMm: b.projectionMm,
              slabThicknessMm: b.slabThicknessMm,
            },
          });
        }
        if (draft.facade.components.some((c) => c.storeyId === storey.id)) {
          // Facade components on this storey go with it; `facade.apply_kit`
          // replaces the whole sub-model, so it restores them exactly.
          inverse.push({
            type: 'facade.apply_kit',
            payload: {
              kitId: draft.facade.kitId,
              seed: draft.facade.seed,
              colorwayId: draft.facade.colorwayId,
              components: draft.facade.components.map((c) => ({
                id: c.id,
                kind: c.kind,
                storeyId: c.storeyId,
                wallId: c.wallId,
                openingId: c.openingId,
                params: c.params,
              })),
            },
          });
        }
      }
      const wallIdsToDrop = new Set(
        draft.walls.filter((x) => x.storeyId === storey.id).map((x) => x.id),
      );
      draft.walls = draft.walls.filter((x) => x.storeyId !== storey.id);
      draft.openings = draft.openings.filter((x) => !wallIdsToDrop.has(x.wallId));
      draft.rooms = draft.rooms.filter((x) => x.storeyId !== storey.id);
      draft.stairs = draft.stairs.filter((x) => x.storeyId !== storey.id);
      draft.slabs = draft.slabs.filter((x) => x.storeyId !== storey.id);
      draft.columns = draft.columns.filter((x) => x.storeyId !== storey.id);
      draft.furniture = draft.furniture.filter((x) => x.storeyId !== storey.id);
      draft.balconies = draft.balconies.filter((x) => x.storeyId !== storey.id);
      draft.facade = {
        ...draft.facade,
        components: draft.facade.components.filter((c) => c.storeyId !== storey.id),
      };
      draft.storeys.splice(index, 1);
      draft.deriveLevels = true;
      break;
    }
    case 'storey.set_height': {
      const idx = draft.storeys.findIndex((s) => s.id === op.payload.storeyId);
      const prev = draft.storeys[idx];
      if (!prev) break;
      push({
        type: 'storey.set_height',
        payload: { storeyId: prev.id, heightMm: prev.heightMm },
      });
      draft.storeys[idx] = { ...prev, heightMm: op.payload.heightMm };
      draft.deriveLevels = true;
      break;
    }

    // ---------------------------------------------------------------- walls
    case 'wall.add': {
      draft.walls.push({
        id: op.payload.id,
        storeyId: op.payload.storeyId,
        a: op.payload.a,
        b: op.payload.b,
        thicknessMm: op.payload.thicknessMm,
        kind: op.payload.kind,
        loadBearing: op.payload.loadBearing ?? op.payload.kind === 'external',
      });
      dirty(draft, op.payload.storeyId);
      push({ type: 'wall.delete', payload: { wallId: op.payload.id } });
      break;
    }
    case 'wall.move': {
      const idx = draft.walls.findIndex((w) => w.id === op.payload.wallId);
      const prev = draft.walls[idx];
      if (!prev) break;
      push({ type: 'wall.move', payload: { wallId: prev.id, a: prev.a, b: prev.b } });
      draft.walls[idx] = { ...prev, a: op.payload.a, b: op.payload.b };
      dirty(draft, prev.storeyId);
      break;
    }
    case 'wall.split': {
      const idx = draft.walls.findIndex((w) => w.id === op.payload.wallId);
      const wall = draft.walls[idx];
      if (!wall) break;
      const splitPt = pointAlongSeg({ a: wall.a, b: wall.b }, op.payload.atMm);
      const movedOpenings = draft.openings.filter(
        (o) => o.wallId === wall.id && o.offsetMm >= op.payload.atMm,
      );
      if (wantInverse) {
        // Order matters: drop the new wall first (its openings go with it), then
        // restore the original geometry, then re-add the openings by their ids.
        inverse.push({ type: 'wall.delete', payload: { wallId: op.payload.newWallId } });
        inverse.push({ type: 'wall.move', payload: { wallId: wall.id, a: wall.a, b: wall.b } });
        for (const o of movedOpenings) {
          inverse.push({ type: 'opening.add', payload: openingAddPayload(o) });
        }
      }
      draft.walls[idx] = { ...wall, b: splitPt };
      draft.walls.push({ ...wall, id: op.payload.newWallId, a: splitPt, b: wall.b });
      draft.openings = draft.openings.map((o) =>
        movedOpenings.some((m) => m.id === o.id)
          ? { ...o, wallId: op.payload.newWallId, offsetMm: o.offsetMm - op.payload.atMm }
          : o,
      );
      dirty(draft, wall.storeyId);
      break;
    }
    case 'wall.delete': {
      const idx = draft.walls.findIndex((w) => w.id === op.payload.wallId);
      const wall = draft.walls[idx];
      if (!wall) break;
      const hosted = draft.openings.filter((o) => o.wallId === wall.id);
      if (wantInverse) {
        inverse.push({
          type: 'wall.add',
          payload: {
            id: wall.id,
            storeyId: wall.storeyId,
            a: wall.a,
            b: wall.b,
            thicknessMm: wall.thicknessMm,
            kind: wall.kind,
            loadBearing: wall.loadBearing,
          },
        });
        for (const o of hosted) {
          inverse.push({ type: 'opening.add', payload: openingAddPayload(o) });
        }
      }
      draft.walls.splice(idx, 1);
      draft.openings = draft.openings.filter((o) => o.wallId !== wall.id);
      draft.facade = {
        ...draft.facade,
        components: draft.facade.components.filter((c) => c.wallId !== wall.id),
      };
      dirty(draft, wall.storeyId);
      break;
    }
    case 'wall.set_thickness': {
      const idx = draft.walls.findIndex((w) => w.id === op.payload.wallId);
      const prev = draft.walls[idx];
      if (!prev) break;
      push({
        type: 'wall.set_thickness',
        payload: { wallId: prev.id, thicknessMm: prev.thicknessMm },
      });
      draft.walls[idx] = { ...prev, thicknessMm: op.payload.thicknessMm };
      dirty(draft, prev.storeyId);
      break;
    }

    // ------------------------------------------------------------- openings
    case 'opening.add': {
      draft.openings.push({
        id: op.payload.id,
        wallId: op.payload.wallId,
        kind: op.payload.kind,
        widthMm: op.payload.widthMm,
        heightMm: op.payload.heightMm,
        sillMm: op.payload.sillMm,
        offsetMm: op.payload.offsetMm,
        swing: op.payload.swing,
        tag: op.payload.tag ?? null,
      });
      touchWall(draft, op.payload.wallId);
      push({ type: 'opening.delete', payload: { openingId: op.payload.id } });
      break;
    }
    case 'opening.move': {
      const idx = draft.openings.findIndex((o) => o.id === op.payload.openingId);
      const prev = draft.openings[idx];
      if (!prev) break;
      push({
        type: 'opening.move',
        payload: { openingId: prev.id, offsetMm: prev.offsetMm, wallId: prev.wallId },
      });
      draft.openings[idx] = {
        ...prev,
        offsetMm: op.payload.offsetMm,
        wallId: op.payload.wallId ?? prev.wallId,
      };
      touchWall(draft, prev.wallId);
      touchWall(draft, op.payload.wallId);
      break;
    }
    case 'opening.resize': {
      const idx = draft.openings.findIndex((o) => o.id === op.payload.openingId);
      const prev = draft.openings[idx];
      if (!prev) break;
      push({
        type: 'opening.resize',
        payload: {
          openingId: prev.id,
          widthMm: prev.widthMm,
          heightMm: prev.heightMm,
          sillMm: prev.sillMm,
        },
      });
      draft.openings[idx] = {
        ...prev,
        widthMm: op.payload.widthMm ?? prev.widthMm,
        heightMm: op.payload.heightMm ?? prev.heightMm,
        sillMm: op.payload.sillMm ?? prev.sillMm,
      };
      touchWall(draft, prev.wallId);
      break;
    }
    case 'opening.flip': {
      const idx = draft.openings.findIndex((o) => o.id === op.payload.openingId);
      const prev = draft.openings[idx];
      if (!prev) break;
      push({ type: 'opening.flip', payload: { openingId: prev.id, swing: prev.swing } });
      draft.openings[idx] = { ...prev, swing: op.payload.swing };
      touchWall(draft, prev.wallId);
      break;
    }
    case 'opening.delete': {
      const idx = draft.openings.findIndex((o) => o.id === op.payload.openingId);
      const prev = draft.openings[idx];
      if (!prev) break;
      push({ type: 'opening.add', payload: openingAddPayload(prev) });
      touchWall(draft, prev.wallId);
      draft.openings.splice(idx, 1);
      draft.facade = {
        ...draft.facade,
        components: draft.facade.components.filter((c) => c.openingId !== prev.id),
      };
      break;
    }

    // ---------------------------------------------------------------- rooms
    case 'room.assign': {
      const idx = draft.rooms.findIndex((r) => r.id === op.payload.roomId);
      const prev = draft.rooms[idx];
      if (!prev) break;
      push({
        type: 'room.assign',
        payload: {
          roomId: prev.id,
          type: prev.type,
          name: prev.name,
          tags: prev.tags,
          locked: prev.locked,
        },
      });
      touch(draft, prev.storeyId);
      draft.rooms[idx] = {
        ...prev,
        type: op.payload.type,
        name: op.payload.name ?? prev.name,
        tags: op.payload.tags ?? prev.tags,
        locked: op.payload.locked ?? prev.locked,
      };
      break;
    }
    case 'room.set_target': {
      const idx = draft.rooms.findIndex((r) => r.id === op.payload.roomId);
      const prev = draft.rooms[idx];
      if (!prev) break;
      push({
        type: 'room.set_target',
        payload: {
          roomId: prev.id,
          targetAreaMm2: prev.targetAreaMm2,
          mustFace: prev.mustFace,
        },
      });
      touch(draft, prev.storeyId);
      draft.rooms[idx] = {
        ...prev,
        targetAreaMm2:
          op.payload.targetAreaMm2 === undefined ? prev.targetAreaMm2 : op.payload.targetAreaMm2,
        mustFace: op.payload.mustFace === undefined ? prev.mustFace : op.payload.mustFace,
      };
      break;
    }

    // --------------------------------------------------------------- stairs
    case 'stair.add': {
      draft.stairs.push({
        id: op.payload.id,
        storeyId: op.payload.storeyId,
        kind: op.payload.kind,
        origin: op.payload.origin,
        direction: op.payload.direction,
        riserMm: op.payload.riserMm,
        treadMm: op.payload.treadMm,
        widthMm: op.payload.widthMm,
        risersCount: op.payload.risersCount,
        landing: op.payload.landing ?? null,
      });
      markStoreyAboveDirty(draft, op.payload.storeyId);
      push({ type: 'stair.delete', payload: { stairId: op.payload.id } });
      break;
    }
    case 'stair.edit': {
      const idx = draft.stairs.findIndex((s) => s.id === op.payload.stairId);
      const prev = draft.stairs[idx];
      if (!prev) break;
      const patch = op.payload.patch;
      if (wantInverse) {
        const invPatch: {
          kind?: Stair['kind'];
          origin?: Pt;
          direction?: Direction4;
          riserMm?: number;
          treadMm?: number;
          widthMm?: number;
          risersCount?: number;
          landing?: Stair['landing'];
        } = {};
        if (patch.kind !== undefined) invPatch.kind = prev.kind;
        if (patch.origin !== undefined) invPatch.origin = prev.origin;
        if (patch.direction !== undefined) invPatch.direction = prev.direction;
        if (patch.riserMm !== undefined) invPatch.riserMm = prev.riserMm;
        if (patch.treadMm !== undefined) invPatch.treadMm = prev.treadMm;
        if (patch.widthMm !== undefined) invPatch.widthMm = prev.widthMm;
        if (patch.risersCount !== undefined) invPatch.risersCount = prev.risersCount;
        if (patch.landing !== undefined) invPatch.landing = prev.landing;
        inverse.push({ type: 'stair.edit', payload: { stairId: prev.id, patch: invPatch } });
      }
      draft.stairs[idx] = {
        ...prev,
        kind: patch.kind ?? prev.kind,
        origin: patch.origin ?? prev.origin,
        direction: patch.direction ?? prev.direction,
        riserMm: patch.riserMm ?? prev.riserMm,
        treadMm: patch.treadMm ?? prev.treadMm,
        widthMm: patch.widthMm ?? prev.widthMm,
        risersCount: patch.risersCount ?? prev.risersCount,
        landing: patch.landing === undefined ? prev.landing : patch.landing,
      };
      markStoreyAboveDirty(draft, prev.storeyId);
      break;
    }
    case 'stair.delete': {
      const idx = draft.stairs.findIndex((s) => s.id === op.payload.stairId);
      const prev = draft.stairs[idx];
      if (!prev) break;
      push({ type: 'stair.add', payload: stairAddPayload(prev) });
      draft.stairs.splice(idx, 1);
      markStoreyAboveDirty(draft, prev.storeyId);
      break;
    }

    // -------------------------------------------------------------- columns
    case 'column.set': {
      const idx = draft.columns.findIndex((c) => c.id === op.payload.id);
      if (op.payload.action === 'add') {
        const column: Column = {
          id: op.payload.id,
          storeyId: op.payload.storeyId as StoreyId,
          pt: op.payload.pt as Pt,
          sizeMm: op.payload.sizeMm ?? DEFAULTS.columnSizeMm,
        };
        draft.columns.push(column);
        push({ type: 'column.set', payload: { action: 'delete', id: column.id } });
      } else if (op.payload.action === 'move' && idx >= 0) {
        const prev = draft.columns[idx];
        if (!prev) break;
        push({
          type: 'column.set',
          payload: { action: 'move', id: prev.id, pt: prev.pt, sizeMm: prev.sizeMm },
        });
        draft.columns[idx] = {
          ...prev,
          pt: op.payload.pt ?? prev.pt,
          sizeMm: op.payload.sizeMm ?? prev.sizeMm,
        };
      } else if (op.payload.action === 'delete' && idx >= 0) {
        const prev = draft.columns[idx];
        if (!prev) break;
        push({
          type: 'column.set',
          payload: {
            action: 'add',
            id: prev.id,
            storeyId: prev.storeyId,
            pt: prev.pt,
            sizeMm: prev.sizeMm,
          },
        });
        draft.columns.splice(idx, 1);
      }
      break;
    }

    // ------------------------------------------------------------ furniture
    case 'furniture.set': {
      const idx = draft.furniture.findIndex((f) => f.id === op.payload.id);
      if (op.payload.action === 'place') {
        const item: FurnitureInstance = {
          id: op.payload.id,
          storeyId: op.payload.storeyId as StoreyId,
          catalogId: op.payload.catalogId ?? '',
          pt: op.payload.pt as Pt,
          rotationDeg: op.payload.rotationDeg ?? 0,
        };
        draft.furniture.push(item);
        push({ type: 'furniture.set', payload: { action: 'delete', id: item.id } });
      } else if (op.payload.action === 'transform' && idx >= 0) {
        const prev = draft.furniture[idx];
        if (!prev) break;
        push({
          type: 'furniture.set',
          payload: {
            action: 'transform',
            id: prev.id,
            pt: prev.pt,
            rotationDeg: prev.rotationDeg,
          },
        });
        draft.furniture[idx] = {
          ...prev,
          pt: op.payload.pt ?? prev.pt,
          rotationDeg: op.payload.rotationDeg ?? prev.rotationDeg,
        };
      } else if (op.payload.action === 'delete' && idx >= 0) {
        const prev = draft.furniture[idx];
        if (!prev) break;
        push({
          type: 'furniture.set',
          payload: {
            action: 'place',
            id: prev.id,
            storeyId: prev.storeyId,
            catalogId: prev.catalogId,
            pt: prev.pt,
            rotationDeg: prev.rotationDeg,
          },
        });
        draft.furniture.splice(idx, 1);
      }
      break;
    }

    // ------------------------------------------------------------ balconies
    case 'balcony.set': {
      const idx = draft.balconies.findIndex((b) => b.id === op.payload.id);
      if (op.payload.action === 'add') {
        const balcony: Balcony = {
          id: op.payload.id,
          storeyId: op.payload.storeyId as StoreyId,
          polygon: (op.payload.polygon ?? []).slice(),
          railingKind: op.payload.railingKind ?? 'ms',
          railingHeightMm: op.payload.railingHeightMm ?? DEFAULTS.railingHeightMm,
          projectionMm: op.payload.projectionMm ?? DEFAULTS.balconyProjectionMm,
          slabThicknessMm: op.payload.slabThicknessMm ?? DEFAULTS.slabThicknessMm,
        };
        draft.balconies.push(balcony);
        push({ type: 'balcony.set', payload: { action: 'delete', id: balcony.id } });
      } else if (op.payload.action === 'edit' && idx >= 0) {
        const prev = draft.balconies[idx];
        if (!prev) break;
        push({
          type: 'balcony.set',
          payload: {
            action: 'edit',
            id: prev.id,
            polygon: prev.polygon,
            railingKind: prev.railingKind,
            railingHeightMm: prev.railingHeightMm,
            projectionMm: prev.projectionMm,
            slabThicknessMm: prev.slabThicknessMm,
          },
        });
        draft.balconies[idx] = {
          ...prev,
          polygon: op.payload.polygon ?? prev.polygon,
          railingKind: op.payload.railingKind ?? prev.railingKind,
          railingHeightMm: op.payload.railingHeightMm ?? prev.railingHeightMm,
          projectionMm: op.payload.projectionMm ?? prev.projectionMm,
          slabThicknessMm: op.payload.slabThicknessMm ?? prev.slabThicknessMm,
        };
      } else if (op.payload.action === 'delete' && idx >= 0) {
        const prev = draft.balconies[idx];
        if (!prev) break;
        push({
          type: 'balcony.set',
          payload: {
            action: 'add',
            id: prev.id,
            storeyId: prev.storeyId,
            polygon: prev.polygon,
            railingKind: prev.railingKind,
            railingHeightMm: prev.railingHeightMm,
            projectionMm: prev.projectionMm,
            slabThicknessMm: prev.slabThicknessMm,
          },
        });
        draft.balconies.splice(idx, 1);
      }
      break;
    }

    // --------------------------------------------------------------- facade
    case 'facade.apply_kit': {
      const prev = draft.facade;
      push({
        type: 'facade.apply_kit',
        payload: {
          kitId: prev.kitId,
          seed: prev.seed,
          colorwayId: prev.colorwayId,
          components: prev.components.map((c) => ({
            id: c.id,
            kind: c.kind,
            storeyId: c.storeyId,
            wallId: c.wallId,
            openingId: c.openingId,
            params: c.params,
          })),
        },
      });
      const components: FacadeComponent[] = op.payload.components.map((c) => ({
        id: c.id,
        kind: c.kind,
        storeyId: c.storeyId ?? null,
        wallId: c.wallId ?? null,
        openingId: c.openingId ?? null,
        // `?? {}` — house-model.schema.json requires `params` on every facade
        // component, and the Python mirror defaults it to {}. Storing `undefined`
        // drops the key from the canonical form and splits the hash.
        params: c.params ?? {},
      }));
      draft.facade = {
        kitId: op.payload.kitId,
        seed: op.payload.seed,
        colorwayId: op.payload.colorwayId ?? null,
        components,
      };
      break;
    }
    case 'facade.edit_component': {
      const idx = draft.facade.components.findIndex((c) => c.id === op.payload.componentId);
      const prev = draft.facade.components[idx];
      if (!prev) break;
      push({
        type: 'facade.edit_component',
        payload: {
          componentId: prev.id,
          patch: invertMergePatch(prev.params, op.payload.patch),
        },
      });
      const components = draft.facade.components.slice();
      components[idx] = { ...prev, params: applyMergePatch(prev.params, op.payload.patch) };
      draft.facade = { ...draft.facade, components };
      break;
    }

    // ------------------------------------------------------------ materials
    case 'material.assign': {
      const idx = draft.materials.findIndex((m) => m.id === op.payload.id);
      if (op.payload.materialId === null) {
        const prev = draft.materials[idx];
        if (!prev) break;
        push({
          type: 'material.assign',
          payload: { id: prev.id, target: prev.target, materialId: prev.materialId },
        });
        draft.materials.splice(idx, 1);
        break;
      }
      if (idx >= 0) {
        const prev = draft.materials[idx];
        if (!prev) break;
        push({
          type: 'material.assign',
          payload: { id: prev.id, target: prev.target, materialId: prev.materialId },
        });
        draft.materials[idx] = {
          ...prev,
          target: normaliseSurfaceGroupRef(op.payload.target),
          materialId: op.payload.materialId,
        };
      } else {
        const assignment: MaterialAssignment = {
          id: op.payload.id,
          target: normaliseSurfaceGroupRef(op.payload.target),
          materialId: op.payload.materialId,
        };
        draft.materials.push(assignment);
        push({
          type: 'material.assign',
          payload: { id: assignment.id, target: assignment.target, materialId: null },
        });
      }
      break;
    }

    // --------------------------------------------------------------- levels
    case 'levels.set': {
      const prev = draft.levels;
      const invPayload: {
        plinthMm?: number;
        sillDefaultMm?: number;
        lintelDefaultMm?: number;
        parapetMm?: number;
        fflPerStoreyMm?: number[];
      } = {};
      if (op.payload.plinthMm !== undefined) invPayload.plinthMm = prev.plinthMm;
      if (op.payload.sillDefaultMm !== undefined) invPayload.sillDefaultMm = prev.sillDefaultMm;
      if (op.payload.lintelDefaultMm !== undefined) {
        invPayload.lintelDefaultMm = prev.lintelDefaultMm;
      }
      if (op.payload.parapetMm !== undefined) invPayload.parapetMm = prev.parapetMm;
      if (op.payload.fflPerStoreyMm !== undefined) {
        invPayload.fflPerStoreyMm = prev.fflPerStoreyMm.slice();
      }
      push({ type: 'levels.set', payload: invPayload });
      draft.levels = {
        plinthMm: op.payload.plinthMm ?? prev.plinthMm,
        sillDefaultMm: op.payload.sillDefaultMm ?? prev.sillDefaultMm,
        lintelDefaultMm: op.payload.lintelDefaultMm ?? prev.lintelDefaultMm,
        parapetMm: op.payload.parapetMm ?? prev.parapetMm,
        fflPerStoreyMm: op.payload.fflPerStoreyMm
          ? op.payload.fflPerStoreyMm.slice()
          : prev.fflPerStoreyMm,
      };
      // An explicit FFL array wins; otherwise plinth changes re-derive them.
      draft.deriveLevels = op.payload.fflPerStoreyMm === undefined;
      break;
    }

    // --------------------------------------------------------------- solver
    case 'solver.apply_option': {
      // Unreachable: `fold` intercepts this op and delegates the expansion to
      // `applyGroup`, so every inner op is validated against the intermediate
      // state instead of being trusted. Kept for switch exhaustiveness.
      break;
    }

    // ---------------------------------------------------------- annotations
    case 'annotation.set': {
      const idx = draft.annotations.findIndex((a) => a.id === op.payload.id);
      if (op.payload.action === 'add') {
        const annotation: Annotation = {
          id: op.payload.id,
          sheetId: op.payload.sheetId as Annotation['sheetId'],
          anchorElementId: op.payload.anchorElementId ?? null,
          anchorKind: op.payload.anchorKind ?? 'sheet',
          payload: op.payload.payload ?? {},
          orphaned: op.payload.orphaned ?? false,
        };
        draft.annotations.push(annotation);
        push({ type: 'annotation.set', payload: { action: 'delete', id: annotation.id } });
      } else if (op.payload.action === 'edit' && idx >= 0) {
        const prev = draft.annotations[idx];
        if (!prev) break;
        push({
          type: 'annotation.set',
          payload: {
            action: 'edit',
            id: prev.id,
            anchorElementId: prev.anchorElementId,
            anchorKind: prev.anchorKind,
            payload: prev.payload,
            orphaned: prev.orphaned,
          },
        });
        draft.annotations[idx] = {
          ...prev,
          anchorElementId:
            op.payload.anchorElementId === undefined
              ? prev.anchorElementId
              : op.payload.anchorElementId,
          anchorKind: op.payload.anchorKind ?? prev.anchorKind,
          payload: op.payload.payload ?? prev.payload,
          orphaned: op.payload.orphaned ?? prev.orphaned,
        };
      } else if (op.payload.action === 'delete' && idx >= 0) {
        const prev = draft.annotations[idx];
        if (!prev) break;
        push({
          type: 'annotation.set',
          payload: {
            action: 'add',
            id: prev.id,
            sheetId: prev.sheetId,
            anchorElementId: prev.anchorElementId,
            anchorKind: prev.anchorKind,
            payload: prev.payload,
            orphaned: prev.orphaned,
          },
        });
        draft.annotations.splice(idx, 1);
      }
      break;
    }
  }
}

/** Mark a storey as needing room+slab recomputation (and validation scope). */
function dirty(draft: Draft, storeyId: string): void {
  draft.dirtyStoreys.add(storeyId);
  draft.touchedStoreys.add(storeyId);
}

/** Mark a storey as touched (validation scope only — geometry did not change). */
function touch(draft: Draft, storeyId: string | undefined): void {
  if (storeyId !== undefined) draft.touchedStoreys.add(storeyId);
}

/** Validation scope for an op that edits an opening: the host wall's storey. */
function touchWall(draft: Draft, wallId: string | undefined): void {
  if (wallId === undefined) return;
  touch(draft, draft.walls.find((w) => w.id === wallId)?.storeyId);
}

/** A stair penetrates the slab of the storey ABOVE it. */
function markStoreyAboveDirty(draft: Draft, storeyId: StoreyId): void {
  const idx = draft.storeys.findIndex((s) => s.id === storeyId);
  draft.touchedStoreys.add(storeyId);
  if (idx >= 0 && idx + 1 < draft.storeys.length) {
    dirty(draft, draft.storeys[idx + 1]!.id);
  }
}

function defaultStoreyName(index: number): string {
  const names = ['Ground Floor', 'First Floor', 'Second Floor', 'Third Floor', 'Fourth Floor'];
  return names[index] ?? `Floor ${String(index)}`;
}

// ---------------------------------------------------------------------------
// CROSS-LANGUAGE CONTRACT: absent optional sub-key -> explicit null
//
// `stateHash` is `sha256(canonicalJson(doc))`, and canonicalJson OMITS keys whose
// value is `undefined` while the Python mirror (apps/api/garh_model) writes an
// explicit `null` for the same absent input. Any object stored in the document
// straight off a wire payload therefore risks two problems at once:
//
//   1. a hash that disagrees with Python's for the same op log — which is the
//      value `design_versions.snapshot_hash` stores and the value the 409-rebase
//      path compares, so a divergence corrupts sync, not just a test; and
//   2. a document that fails its own JSON Schema, because
//      common.schema.json's $defs.LevelData and $defs.SurfaceGroupRef both list
//      every key in `required` with `additionalProperties: false`.
//
// The fix is one-sided on purpose: normalise on the way IN, so the stored
// document is always fully populated and no later reader has to care. The two
// helpers below are the only sanctioned way to store a LevelData or a
// SurfaceGroupRef that came from a payload. `validateOpShape` deliberately keeps
// accepting the short forms — rejecting them would be a wire-compat break.
// ---------------------------------------------------------------------------

/** A `LevelData` with all four schema-required keys present. */
function normaliseLevel(level: LevelData | undefined): LevelData {
  if (level === undefined) return defaultLevelData(0);
  return {
    fflMm: level.fflMm,
    slabThicknessMm: level.slabThicknessMm,
    sillDefaultMm: level.sillDefaultMm ?? null,
    lintelDefaultMm: level.lintelDefaultMm ?? null,
  };
}

/** A `SurfaceGroupRef` with `storeyId` and `elementId` present (null when unset). */
function normaliseSurfaceGroupRef(target: SurfaceGroupRef): SurfaceGroupRef {
  return {
    group: target.group,
    storeyId: target.storeyId ?? null,
    elementId: target.elementId ?? null,
  };
}

function openingAddPayload(o: Opening): Extract<Op, { type: 'opening.add' }>['payload'] {
  return {
    id: o.id,
    wallId: o.wallId,
    kind: o.kind,
    widthMm: o.widthMm,
    heightMm: o.heightMm,
    sillMm: o.sillMm,
    offsetMm: o.offsetMm,
    swing: o.swing,
    tag: o.tag,
  };
}

function stairAddPayload(s: Stair): Extract<Op, { type: 'stair.add' }>['payload'] {
  return {
    id: s.id,
    storeyId: s.storeyId,
    kind: s.kind,
    origin: s.origin,
    direction: s.direction,
    riserMm: s.riserMm,
    treadMm: s.treadMm,
    widthMm: s.widthMm,
    risersCount: s.risersCount,
    landing: s.landing,
  };
}

// ---------------------------------------------------------------------------
// Groups, replay, undo/redo
// ---------------------------------------------------------------------------

export interface GroupResult {
  readonly model: ProjectDoc;
  /** Inverse of the WHOLE group: reversed concatenation of per-op inverses. */
  readonly inverse: readonly Op[];
  /** The ops as applied, with `groupId` stamped on each. */
  readonly ops: readonly Op[];
}

/**
 * Apply several ops ATOMICALLY: if any op is rejected, nothing is applied and
 * the `OpRejectedError` propagates. Undo/redo works on the group, not the ops.
 */
export function applyGroup(model: ProjectDoc, ops: readonly Op[], groupId?: GroupId): GroupResult {
  let current = model;
  const inverses: Op[][] = [];
  const applied: Op[] = [];
  for (const op of ops) {
    const stamped: Op = groupId === undefined ? op : ({ ...op, groupId } as Op);
    const result = fold(current, stamped);
    current = result.model;
    inverses.push(result.inverse.slice());
    applied.push(stamped);
  }
  const inverse: Op[] = [];
  for (let i = inverses.length - 1; i >= 0; i--) {
    for (const invOp of inverses[i]!) {
      inverse.push(groupId === undefined ? invOp : ({ ...invOp, groupId } as Op));
    }
  }
  return { model: current, inverse, ops: applied };
}

/** Fold an op log from `initial` (default: an empty document). */
export function replay(ops: readonly Op[], initial?: ProjectDoc): ProjectDoc {
  let current = initial ?? emptyProjectDoc();
  for (const op of ops) {
    current = fold(current, op, { computeInverse: false }).model;
  }
  return current;
}

/** One undoable unit of work. */
export interface UndoEntry {
  readonly groupId: string;
  /** The ops as applied (redo replays these). */
  readonly ops: readonly Op[];
  /** The ops that undo them, in order. */
  readonly inverse: readonly Op[];
  /** Short label for the undo toast: "Wall deleted". */
  readonly label?: string;
}

/**
 * Undo/redo over GROUPS (§4 batching). Holds only op lists, never snapshots, so
 * a 1000-step history costs kilobytes.
 *
 * The stack is a value object: `undo`/`redo` take the current model and return
 * the new one, so the Zustand store stays the single writer.
 */
export class UndoStack {
  private undoStack: UndoEntry[] = [];
  private redoStack: UndoEntry[] = [];
  private readonly limit: number;

  constructor(limit = 200) {
    this.limit = limit;
  }

  /** Record an applied group. Clears the redo stack (new branch of history). */
  push(entry: UndoEntry): void {
    this.undoStack.push(entry);
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack = [];
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }
  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }
  get undoDepth(): number {
    return this.undoStack.length;
  }
  get redoDepth(): number {
    return this.redoStack.length;
  }
  /** Label of the group that would be undone next (for the toast/menu). */
  get nextUndoLabel(): string | undefined {
    return this.undoStack[this.undoStack.length - 1]?.label;
  }
  get nextRedoLabel(): string | undefined {
    return this.redoStack[this.redoStack.length - 1]?.label;
  }

  /** Apply the inverse of the last group. Returns null when there is nothing to undo. */
  undo(model: ProjectDoc): { model: ProjectDoc; entry: UndoEntry } | null {
    const entry = this.undoStack.pop();
    if (!entry) return null;
    const result = applyGroup(model, entry.inverse, entry.groupId as GroupId);
    this.redoStack.push(entry);
    return { model: result.model, entry };
  }

  /** Re-apply the last undone group. Returns null when there is nothing to redo. */
  redo(model: ProjectDoc): { model: ProjectDoc; entry: UndoEntry } | null {
    const entry = this.redoStack.pop();
    if (!entry) return null;
    const result = applyGroup(model, entry.ops, entry.groupId as GroupId);
    this.undoStack.push(entry);
    return { model: result.model, entry };
  }

  clear(): void {
    this.undoStack = [];
    this.redoStack = [];
  }

  /** Serialisable snapshot of the history (for session restore). */
  toJSON(): { undo: UndoEntry[]; redo: UndoEntry[] } {
    return { undo: this.undoStack.slice(), redo: this.redoStack.slice() };
  }

  static fromJSON(data: { undo: UndoEntry[]; redo: UndoEntry[] }, limit = 200): UndoStack {
    const stack = new UndoStack(limit);
    stack.undoStack = data.undo.slice();
    stack.redoStack = data.redo.slice();
    return stack;
  }
}

// ---------------------------------------------------------------------------
// Misc derived reads that need fold's helpers
// ---------------------------------------------------------------------------

/** Clear length of a wall centreline in mm. */
export function wallLengthMm(wall: Wall): number {
  return segmentLengthMm({ a: wall.a, b: wall.b });
}

/** Total floor area of a storey's rooms in mm² (carpet area). */
export function storeyCarpetAreaMm2(doc: ProjectDoc, storeyId: StoreyId): number {
  let total = 0;
  for (const room of doc.house.rooms) {
    if (room.storeyId === storeyId) total += room.areaMm2;
  }
  return total;
}

/** Built-up area of a storey in mm² (slab minus cut-outs). */
export function storeyBuiltUpAreaMm2(doc: ProjectDoc, storeyId: StoreyId): number {
  let total = 0;
  for (const slab of doc.house.slabs) {
    if (slab.storeyId !== storeyId || slab.kind !== 'floor') continue;
    total += polygonAreaMm2(slab.polygon);
    for (const cut of slab.cutouts) total -= polygonAreaMm2(cut);
  }
  return total;
}

/** Ids of rooms currently locked against solver re-solve (§5.7). */
export function lockedRoomIds(doc: ProjectDoc): RoomId[] {
  return doc.house.rooms.filter((r) => r.locked).map((r) => r.id);
}

/** Assert the document is at the schema version this build understands. */
export function assertSchemaVersion(doc: ProjectDoc): void {
  if (doc.schemaVersion !== SCHEMA_VERSION) {
    throw new Error(
      `Document schemaVersion ${String(doc.schemaVersion)} is not supported by this build (expected ${String(SCHEMA_VERSION)}).`,
    );
  }
}
