/**
 * validate.ts — the fold invariants (playbook §3) with MACHINE-READABLE
 * rejection reasons.
 *
 * WHY THE CODES ARE AN API: §10's copilot pipeline dry-run-folds candidate ops,
 * and when they are rejected it feeds the reasons back to the LLM for ONE
 * self-correction pass. That only works if the reason is structured — a code, a
 * human message, the offending element ids, and where possible the actual value
 * and the limit. Never throw a bare string out of this module; never change a
 * code without changing the copilot fixtures.
 *
 * Invariants enforced (§3, verbatim):
 *   - walls have non-zero length
 *   - openings fit within the host wall length minus 115mm end margins
 *   - opening sill + height ≤ storey height
 *   - stairs' risersCount × riserMm ≈ storey height ±10mm
 *   - no two walls exactly overlap
 *   - rooms closed
 * Plus the structural preconditions an op needs to be applicable at all
 * (referenced element exists, id not already taken, integer mm everywhere).
 */

import {
  collinearOverlap,
  polygonAreaMm2,
  polygonIsClosedRing,
  ptEq,
  segmentLengthMm,
} from './geometry';
import type { Pt } from './geometry';
import { isIdOf, tryParseId } from './ids';
import type { ElementType } from './ids';
import {
  ANNOTATION_ANCHOR_KINDS,
  DIRECTIONS_4,
  FACADE_COMPONENT_KINDS,
  DIRECTIONS_8,
  OPENING_KINDS,
  OPENING_SWINGS,
  RAILING_KINDS,
  ROOM_TYPES,
  STAIR_KINDS,
  SURFACE_GROUPS,
  VASTU_MODES,
  WALL_KINDS,
} from './model';
import type { ProjectDoc } from './model';
import {
  ANNOTATION_ACTIONS,
  BALCONY_ACTIONS,
  COLUMN_ACTIONS,
  FURNITURE_ACTIONS,
  getOpSpec,
} from './ops';
import type { Op } from './ops';
import { isIntMm, roundHalfAwayFromZero } from './units';

/** §3: openings must keep this much solid wall at each end. */
export const WALL_END_MARGIN_MM = 115;

/** §3: risersCount × riserMm must match storey height within this. */
export const STAIR_RISE_TOLERANCE_MM = 10;

/** Sanity ceiling on a wall thickness (a 1m wall is a data-entry error). */
export const MAX_WALL_THICKNESS_MM = 1000;

/** Rooms smaller than this are noise from the planar subdivision, not rooms. */
export const MIN_ROOM_AREA_MM2 = 500_000; // 0.5 m²

/**
 * Every rejection code. STABLE API — the copilot, the UI error copy map and the
 * API problem+json `code` field all key off these strings.
 */
export const VALIDATION_CODES = [
  // --- op envelope / payload
  'OP_UNKNOWN_TYPE',
  'OP_PAYLOAD_NOT_OBJECT',
  'OP_FIELD_MISSING',
  'OP_FIELD_NOT_INT_MM',
  'OP_FIELD_NOT_INT',
  'OP_FIELD_NOT_STRING',
  'OP_FIELD_NOT_OBJECT',
  'OP_FIELD_BAD_ENUM',
  'OP_FIELD_BAD_ID',
  'OP_FIELD_BAD_POINT',
  'OP_FIELD_BAD_POLYGON',
  'OP_FIELD_OUT_OF_RANGE',
  'OP_ACTION_UNKNOWN',
  'OP_ID_ALREADY_EXISTS',
  // --- referenced elements
  'STOREY_UNKNOWN',
  'STOREY_INDEX_OUT_OF_RANGE',
  'WALL_UNKNOWN',
  'OPENING_UNKNOWN',
  'ROOM_UNKNOWN',
  'STAIR_UNKNOWN',
  'COLUMN_UNKNOWN',
  'FURNITURE_UNKNOWN',
  'BALCONY_UNKNOWN',
  'FACADE_COMPONENT_UNKNOWN',
  'MATERIAL_ASSIGNMENT_UNKNOWN',
  'ANNOTATION_UNKNOWN',
  'PLOT_EDGE_UNKNOWN',
  // --- model invariants (§3)
  'WALL_ZERO_LENGTH',
  'WALL_THICKNESS_INVALID',
  'WALL_DUPLICATE',
  'WALL_SPLIT_OUT_OF_RANGE',
  'OPENING_DIMENSION_INVALID',
  'OPENING_OUT_OF_WALL',
  'OPENING_EXCEEDS_STOREY_HEIGHT',
  'OPENING_SILL_INVALID',
  'STAIR_RISE_MISMATCH',
  'STAIR_DIMENSION_INVALID',
  'ROOM_NOT_CLOSED',
  'STOREY_HEIGHT_INVALID',
  'PLOT_BOUNDARY_NOT_CLOSED',
  'PLOT_NORTH_INVALID',
  'LEVELS_INVALID',
  'BALCONY_POLYGON_INVALID',
  'COLUMN_SIZE_INVALID',
  'DUPLICATE_ELEMENT_ID',
  'SCHEMA_VERSION_UNSUPPORTED',
] as const;

export type ValidationCode = (typeof VALIDATION_CODES)[number];

export type Severity = 'error' | 'warning';

/**
 * One machine-readable rejection reason.
 * `message` is user-facing copy (Golden Rule 9: say what to do next).
 */
export interface ValidationIssue {
  readonly code: ValidationCode;
  readonly message: string;
  readonly severity: Severity;
  /** Element ids the issue is about — drives canvas highlighting. */
  readonly elementIds: readonly string[];
  /** Payload path the issue is about, e.g. `payload.widthMm`. */
  readonly field?: string;
  readonly actual?: number | string | null;
  readonly limit?: number | string | null;
  /** One-line suggestion the copilot can act on. */
  readonly fix?: string;
}

/** Thrown by `fold()` when an op cannot be applied. */
export class OpRejectedError extends Error {
  readonly code = 'OP_REJECTED';
  readonly opType: string;
  readonly issues: readonly ValidationIssue[];

  constructor(opType: string, issues: readonly ValidationIssue[]) {
    const first = issues[0];
    super(
      `Op ${opType} rejected: ${first ? `${first.code} — ${first.message}` : 'unknown reason'}`,
    );
    this.name = 'OpRejectedError';
    this.opType = opType;
    this.issues = issues;
  }
}

function issue(
  code: ValidationCode,
  message: string,
  extra: Partial<Omit<ValidationIssue, 'code' | 'message'>> = {},
): ValidationIssue {
  return {
    code,
    message,
    severity: extra.severity ?? 'error',
    elementIds: extra.elementIds ?? [],
    ...(extra.field === undefined ? {} : { field: extra.field }),
    ...(extra.actual === undefined ? {} : { actual: extra.actual }),
    ...(extra.limit === undefined ? {} : { limit: extra.limit }),
    ...(extra.fix === undefined ? {} : { fix: extra.fix }),
  };
}

// ---------------------------------------------------------------------------
// Small field checkers (shared by op-shape validation)
// ---------------------------------------------------------------------------

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function isPt(v: unknown): v is Pt {
  return isPlainObject(v) && isIntMm((v as { x?: unknown }).x) && isIntMm((v as { y?: unknown }).y);
}

function checkIntMm(
  out: ValidationIssue[],
  value: unknown,
  field: string,
  opts: { min?: number; max?: number } = {},
): boolean {
  if (!isIntMm(value)) {
    out.push(
      issue('OP_FIELD_NOT_INT_MM', `${field} must be a whole number of millimetres.`, {
        field,
        actual: typeof value === 'number' ? value : String(value),
        fix: `Send ${field} as an integer count of millimetres (e.g. 3810 for 12'-6").`,
      }),
    );
    return false;
  }
  if (opts.min !== undefined && value < opts.min) {
    out.push(
      issue('OP_FIELD_OUT_OF_RANGE', `${field} must be at least ${String(opts.min)}mm.`, {
        field,
        actual: value,
        limit: opts.min,
      }),
    );
    return false;
  }
  if (opts.max !== undefined && value > opts.max) {
    out.push(
      issue('OP_FIELD_OUT_OF_RANGE', `${field} must be at most ${String(opts.max)}mm.`, {
        field,
        actual: value,
        limit: opts.max,
      }),
    );
    return false;
  }
  return true;
}

function checkInt(
  out: ValidationIssue[],
  value: unknown,
  field: string,
  opts: { min?: number; max?: number } = {},
): boolean {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    out.push(
      issue('OP_FIELD_NOT_INT', `${field} must be an integer.`, {
        field,
        actual: typeof value === 'number' ? value : String(value),
      }),
    );
    return false;
  }
  if (opts.min !== undefined && value < opts.min) {
    out.push(
      issue('OP_FIELD_OUT_OF_RANGE', `${field} must be at least ${String(opts.min)}.`, {
        field,
        actual: value,
        limit: opts.min,
      }),
    );
    return false;
  }
  if (opts.max !== undefined && value > opts.max) {
    out.push(
      issue('OP_FIELD_OUT_OF_RANGE', `${field} must be at most ${String(opts.max)}.`, {
        field,
        actual: value,
        limit: opts.max,
      }),
    );
    return false;
  }
  return true;
}

function checkString(out: ValidationIssue[], value: unknown, field: string): boolean {
  if (typeof value !== 'string') {
    out.push(issue('OP_FIELD_NOT_STRING', `${field} must be a string.`, { field }));
    return false;
  }
  return true;
}

function checkEnum(
  out: ValidationIssue[],
  value: unknown,
  field: string,
  allowed: readonly string[],
): boolean {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    out.push(
      issue('OP_FIELD_BAD_ENUM', `${field} must be one of: ${allowed.join(', ')}.`, {
        field,
        actual: typeof value === 'string' ? value : String(value),
        limit: allowed.join('|'),
      }),
    );
    return false;
  }
  return true;
}

function checkId(
  out: ValidationIssue[],
  value: unknown,
  field: string,
  type: ElementType,
): boolean {
  if (!isIdOf(type, value)) {
    out.push(
      issue('OP_FIELD_BAD_ID', `${field} must be a ${type} id of the form ${type}_<ulid>.`, {
        field,
        actual: typeof value === 'string' ? value : String(value),
        limit: `${type}_<ulid>`,
      }),
    );
    return false;
  }
  return true;
}

function checkPt(out: ValidationIssue[], value: unknown, field: string): boolean {
  if (!isPt(value)) {
    out.push(
      issue('OP_FIELD_BAD_POINT', `${field} must be { x, y } in whole millimetres.`, { field }),
    );
    return false;
  }
  return true;
}

function checkPolygon(out: ValidationIssue[], value: unknown, field: string): boolean {
  if (!Array.isArray(value) || value.length < 3 || !value.every((p) => isPt(p))) {
    out.push(
      issue('OP_FIELD_BAD_POLYGON', `${field} must be at least 3 integer-mm points.`, { field }),
    );
    return false;
  }
  if (!polygonIsClosedRing(value)) {
    out.push(
      issue(
        'OP_FIELD_BAD_POLYGON',
        `${field} must be a closed simple ring with non-zero area (no self-intersections, no repeated vertices).`,
        { field, fix: 'Remove crossing edges or duplicate points.' },
      ),
    );
    return false;
  }
  return true;
}

function checkObject(out: ValidationIssue[], value: unknown, field: string): boolean {
  if (!isPlainObject(value)) {
    out.push(issue('OP_FIELD_NOT_OBJECT', `${field} must be an object.`, { field }));
    return false;
  }
  return true;
}

/**
 * Free-form JSON (brief patches, reg-profile overrides, facade params,
 * annotation payloads) ends up inside the document, and `canonicalJson` refuses
 * to serialise a float. Catch it HERE, where we can name the field and suggest a
 * fix, instead of at hash time where the only symptom is a thrown error.
 */
function checkJsonIntegral(out: ValidationIssue[], value: unknown, field: string): boolean {
  let ok = true;
  const walk = (v: unknown, path: string): void => {
    if (v === null || typeof v === 'string' || typeof v === 'boolean') return;
    if (typeof v === 'number') {
      if (!Number.isSafeInteger(v)) {
        ok = false;
        out.push(
          issue(
            'OP_FIELD_NOT_INT',
            `${path} must be a whole number — this document holds no floats.`,
            {
              field: path,
              actual: v,
              fix: 'Scale the value to an integer (whole rupees, mm, tenths of a degree, basis points).',
            },
          ),
        );
      }
      return;
    }
    if (Array.isArray(v)) {
      v.forEach((item, i) => walk(item, `${path}[${String(i)}]`));
      return;
    }
    if (isPlainObject(v)) {
      for (const key of Object.keys(v)) walk(v[key], `${path}.${key}`);
      return;
    }
    ok = false;
    out.push(
      issue(
        'OP_FIELD_NOT_OBJECT',
        `${path} must be JSON (null, boolean, integer, string, array or object).`,
        {
          field: path,
        },
      ),
    );
  };
  walk(value, field);
  return ok;
}

function has(payload: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(payload, key) && payload[key] !== undefined;
}

function requireField(
  out: ValidationIssue[],
  payload: Record<string, unknown>,
  key: string,
  opType: string,
): boolean {
  if (!has(payload, key)) {
    out.push(
      issue('OP_FIELD_MISSING', `${opType} needs payload.${key}.`, {
        field: `payload.${key}`,
        fix: `Add ${key} to the payload.`,
      }),
    );
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Op shape validation (no document needed)
// ---------------------------------------------------------------------------

/**
 * Validate an op's SHAPE: known type, required fields present, ids well-formed,
 * lengths integer mm, enums legal. Does not look at the document.
 */
export function validateOpShape(op: unknown): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  if (!isPlainObject(op)) {
    return [issue('OP_PAYLOAD_NOT_OBJECT', 'An op must be an object { type, payload }.')];
  }
  const type = op.type;
  if (typeof type !== 'string' || getOpSpec(type) === undefined) {
    return [
      issue('OP_UNKNOWN_TYPE', `Unknown op type ${JSON.stringify(type)}.`, {
        field: 'type',
        actual: typeof type === 'string' ? type : String(type),
        fix: 'Use one of the op types in OP_CATALOG.',
      }),
    ];
  }
  if (!isPlainObject(op.payload)) {
    return [
      issue('OP_PAYLOAD_NOT_OBJECT', `${type} needs an object payload.`, { field: 'payload' }),
    ];
  }
  const p = op.payload;
  const f = (k: string): string => `payload.${k}`;

  switch (type) {
    case 'plot.set_boundary': {
      // An empty polygon is the legal "clear the boundary" form — it is what the
      // inverse of the FIRST plot.set_boundary has to be.
      if (requireField(out, p, 'polygon', type)) {
        if (!Array.isArray(p.polygon)) {
          out.push(
            issue('OP_FIELD_BAD_POLYGON', 'payload.polygon must be an array of points.', {
              field: f('polygon'),
            }),
          );
        } else if (p.polygon.length > 0) {
          checkPolygon(out, p.polygon, f('polygon'));
        }
      }
      if (has(p, 'source')) checkString(out, p.source, f('source'));
      break;
    }
    case 'plot.set_north': {
      if (requireField(out, p, 'deg', type)) checkInt(out, p.deg, f('deg'), { min: 0, max: 359 });
      break;
    }
    case 'plot.set_road': {
      if (requireField(out, p, 'edgeIndex', type))
        checkInt(out, p.edgeIndex, f('edgeIndex'), { min: 0 });
      if (requireField(out, p, 'widthMm', type) && p.widthMm !== null) {
        checkIntMm(out, p.widthMm, f('widthMm'), { min: 1 });
      }
      if (has(p, 'name') && p.name !== null) checkString(out, p.name, f('name'));
      break;
    }
    case 'plot.set_reg_profile': {
      if (requireField(out, p, 'cityPack', type) && p.cityPack !== null) {
        checkString(out, p.cityPack, f('cityPack'));
      }
      if (
        requireField(out, p, 'overrides', type) &&
        checkObject(out, p.overrides, f('overrides'))
      ) {
        checkJsonIntegral(out, p.overrides, f('overrides'));
      }
      break;
    }
    case 'brief.update': {
      if (requireField(out, p, 'patch', type) && checkObject(out, p.patch, f('patch'))) {
        checkJsonIntegral(out, p.patch, f('patch'));
      }
      if (has(p, 'vastuMode')) checkEnum(out, p.vastuMode, f('vastuMode'), VASTU_MODES);
      if (has(p, 'completeness'))
        checkInt(out, p.completeness, f('completeness'), { min: 0, max: 100 });
      break;
    }
    case 'storey.add': {
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'storey');
      if (requireField(out, p, 'index', type)) checkInt(out, p.index, f('index'), { min: 0 });
      if (requireField(out, p, 'heightMm', type)) {
        checkIntMm(out, p.heightMm, f('heightMm'), { min: 1800, max: 12000 });
      }
      if (has(p, 'name')) checkString(out, p.name, f('name'));
      if (has(p, 'level') && checkObject(out, p.level, f('level'))) {
        const lvl = p.level as Record<string, unknown>;
        checkIntMm(out, lvl.fflMm, f('level.fflMm'));
        checkIntMm(out, lvl.slabThicknessMm, f('level.slabThicknessMm'), { min: 1 });
      }
      break;
    }
    case 'storey.remove': {
      if (requireField(out, p, 'index', type)) checkInt(out, p.index, f('index'), { min: 0 });
      break;
    }
    case 'storey.set_height': {
      if (requireField(out, p, 'storeyId', type)) checkId(out, p.storeyId, f('storeyId'), 'storey');
      if (requireField(out, p, 'heightMm', type)) {
        checkIntMm(out, p.heightMm, f('heightMm'), { min: 1800, max: 12000 });
      }
      break;
    }
    case 'wall.add': {
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'wall');
      if (requireField(out, p, 'storeyId', type)) checkId(out, p.storeyId, f('storeyId'), 'storey');
      const okA = requireField(out, p, 'a', type) && checkPt(out, p.a, f('a'));
      const okB = requireField(out, p, 'b', type) && checkPt(out, p.b, f('b'));
      if (okA && okB && ptEq(p.a as Pt, p.b as Pt)) {
        out.push(
          issue('WALL_ZERO_LENGTH', 'A wall needs two different endpoints.', {
            elementIds: [String(p.id)],
            field: f('b'),
            fix: 'Give the wall a non-zero length.',
          }),
        );
      }
      if (requireField(out, p, 'thicknessMm', type)) {
        checkIntMm(out, p.thicknessMm, f('thicknessMm'), { min: 1, max: MAX_WALL_THICKNESS_MM });
      }
      if (requireField(out, p, 'kind', type)) checkEnum(out, p.kind, f('kind'), WALL_KINDS);
      break;
    }
    case 'wall.move': {
      if (requireField(out, p, 'wallId', type)) checkId(out, p.wallId, f('wallId'), 'wall');
      const okA = requireField(out, p, 'a', type) && checkPt(out, p.a, f('a'));
      const okB = requireField(out, p, 'b', type) && checkPt(out, p.b, f('b'));
      if (okA && okB && ptEq(p.a as Pt, p.b as Pt)) {
        out.push(
          issue('WALL_ZERO_LENGTH', 'A wall needs two different endpoints.', {
            elementIds: [String(p.wallId)],
            field: f('b'),
          }),
        );
      }
      break;
    }
    case 'wall.split': {
      if (requireField(out, p, 'wallId', type)) checkId(out, p.wallId, f('wallId'), 'wall');
      if (requireField(out, p, 'newWallId', type))
        checkId(out, p.newWallId, f('newWallId'), 'wall');
      if (requireField(out, p, 'atMm', type)) checkIntMm(out, p.atMm, f('atMm'), { min: 1 });
      break;
    }
    case 'wall.delete': {
      if (requireField(out, p, 'wallId', type)) checkId(out, p.wallId, f('wallId'), 'wall');
      break;
    }
    case 'wall.set_thickness': {
      if (requireField(out, p, 'wallId', type)) checkId(out, p.wallId, f('wallId'), 'wall');
      if (requireField(out, p, 'thicknessMm', type)) {
        checkIntMm(out, p.thicknessMm, f('thicknessMm'), { min: 1, max: MAX_WALL_THICKNESS_MM });
      }
      break;
    }
    case 'opening.add': {
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'opening');
      if (requireField(out, p, 'wallId', type)) checkId(out, p.wallId, f('wallId'), 'wall');
      if (requireField(out, p, 'kind', type)) checkEnum(out, p.kind, f('kind'), OPENING_KINDS);
      if (requireField(out, p, 'widthMm', type))
        checkIntMm(out, p.widthMm, f('widthMm'), { min: 1 });
      if (requireField(out, p, 'heightMm', type))
        checkIntMm(out, p.heightMm, f('heightMm'), { min: 1 });
      if (requireField(out, p, 'sillMm', type)) checkIntMm(out, p.sillMm, f('sillMm'), { min: 0 });
      if (requireField(out, p, 'offsetMm', type))
        checkIntMm(out, p.offsetMm, f('offsetMm'), { min: 0 });
      if (requireField(out, p, 'swing', type)) checkEnum(out, p.swing, f('swing'), OPENING_SWINGS);
      if (has(p, 'tag') && p.tag !== null) checkString(out, p.tag, f('tag'));
      break;
    }
    case 'opening.move': {
      if (requireField(out, p, 'openingId', type))
        checkId(out, p.openingId, f('openingId'), 'opening');
      if (requireField(out, p, 'offsetMm', type))
        checkIntMm(out, p.offsetMm, f('offsetMm'), { min: 0 });
      if (has(p, 'wallId')) checkId(out, p.wallId, f('wallId'), 'wall');
      break;
    }
    case 'opening.resize': {
      if (requireField(out, p, 'openingId', type))
        checkId(out, p.openingId, f('openingId'), 'opening');
      if (has(p, 'widthMm')) checkIntMm(out, p.widthMm, f('widthMm'), { min: 1 });
      if (has(p, 'heightMm')) checkIntMm(out, p.heightMm, f('heightMm'), { min: 1 });
      if (has(p, 'sillMm')) checkIntMm(out, p.sillMm, f('sillMm'), { min: 0 });
      if (!has(p, 'widthMm') && !has(p, 'heightMm') && !has(p, 'sillMm')) {
        out.push(
          issue(
            'OP_FIELD_MISSING',
            'opening.resize needs at least one of widthMm, heightMm, sillMm.',
            {
              field: 'payload',
            },
          ),
        );
      }
      break;
    }
    case 'opening.flip': {
      if (requireField(out, p, 'openingId', type))
        checkId(out, p.openingId, f('openingId'), 'opening');
      if (requireField(out, p, 'swing', type)) checkEnum(out, p.swing, f('swing'), OPENING_SWINGS);
      break;
    }
    case 'opening.delete': {
      if (requireField(out, p, 'openingId', type))
        checkId(out, p.openingId, f('openingId'), 'opening');
      break;
    }
    case 'room.assign': {
      if (requireField(out, p, 'roomId', type)) checkId(out, p.roomId, f('roomId'), 'room');
      if (requireField(out, p, 'type', type)) checkEnum(out, p.type, f('type'), ROOM_TYPES);
      if (has(p, 'name')) checkString(out, p.name, f('name'));
      if (has(p, 'tags')) {
        if (!Array.isArray(p.tags) || !p.tags.every((t) => typeof t === 'string')) {
          out.push(
            issue('OP_FIELD_NOT_STRING', 'payload.tags must be an array of strings.', {
              field: f('tags'),
            }),
          );
        }
      }
      break;
    }
    case 'room.set_target': {
      if (requireField(out, p, 'roomId', type)) checkId(out, p.roomId, f('roomId'), 'room');
      if (has(p, 'targetAreaMm2') && p.targetAreaMm2 !== null) {
        checkInt(out, p.targetAreaMm2, f('targetAreaMm2'), { min: 1 });
      }
      if (has(p, 'mustFace') && p.mustFace !== null) {
        checkEnum(out, p.mustFace, f('mustFace'), DIRECTIONS_8);
      }
      break;
    }
    case 'stair.add': {
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'stair');
      if (requireField(out, p, 'storeyId', type)) checkId(out, p.storeyId, f('storeyId'), 'storey');
      if (requireField(out, p, 'kind', type)) checkEnum(out, p.kind, f('kind'), STAIR_KINDS);
      if (requireField(out, p, 'origin', type)) checkPt(out, p.origin, f('origin'));
      if (requireField(out, p, 'direction', type))
        checkEnum(out, p.direction, f('direction'), DIRECTIONS_4);
      if (requireField(out, p, 'riserMm', type))
        checkIntMm(out, p.riserMm, f('riserMm'), { min: 50, max: 400 });
      if (requireField(out, p, 'treadMm', type))
        checkIntMm(out, p.treadMm, f('treadMm'), { min: 100, max: 600 });
      if (requireField(out, p, 'widthMm', type))
        checkIntMm(out, p.widthMm, f('widthMm'), { min: 300 });
      if (requireField(out, p, 'risersCount', type))
        checkInt(out, p.risersCount, f('risersCount'), { min: 2, max: 60 });
      if (has(p, 'landing') && p.landing !== null && checkObject(out, p.landing, f('landing'))) {
        const l = p.landing as Record<string, unknown>;
        checkIntMm(out, l.widthMm, f('landing.widthMm'), { min: 1 });
        checkIntMm(out, l.depthMm, f('landing.depthMm'), { min: 1 });
      }
      break;
    }
    case 'stair.edit': {
      if (requireField(out, p, 'stairId', type)) checkId(out, p.stairId, f('stairId'), 'stair');
      if (requireField(out, p, 'patch', type) && checkObject(out, p.patch, f('patch'))) {
        const patch = p.patch as Record<string, unknown>;
        if (has(patch, 'kind')) checkEnum(out, patch.kind, f('patch.kind'), STAIR_KINDS);
        if (has(patch, 'origin')) checkPt(out, patch.origin, f('patch.origin'));
        if (has(patch, 'direction'))
          checkEnum(out, patch.direction, f('patch.direction'), DIRECTIONS_4);
        if (has(patch, 'riserMm'))
          checkIntMm(out, patch.riserMm, f('patch.riserMm'), { min: 50, max: 400 });
        if (has(patch, 'treadMm'))
          checkIntMm(out, patch.treadMm, f('patch.treadMm'), { min: 100, max: 600 });
        if (has(patch, 'widthMm')) checkIntMm(out, patch.widthMm, f('patch.widthMm'), { min: 300 });
        if (has(patch, 'risersCount'))
          checkInt(out, patch.risersCount, f('patch.risersCount'), { min: 2, max: 60 });
      }
      break;
    }
    case 'stair.delete': {
      if (requireField(out, p, 'stairId', type)) checkId(out, p.stairId, f('stairId'), 'stair');
      break;
    }
    case 'column.set': {
      if (requireField(out, p, 'action', type))
        checkEnum(out, p.action, f('action'), COLUMN_ACTIONS);
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'column');
      if (p.action === 'add') {
        if (requireField(out, p, 'storeyId', type))
          checkId(out, p.storeyId, f('storeyId'), 'storey');
        if (requireField(out, p, 'pt', type)) checkPt(out, p.pt, f('pt'));
      }
      if (p.action === 'move' && requireField(out, p, 'pt', type)) checkPt(out, p.pt, f('pt'));
      if (has(p, 'sizeMm') && checkObject(out, p.sizeMm, f('sizeMm'))) {
        const s = p.sizeMm as Record<string, unknown>;
        checkIntMm(out, s.xMm, f('sizeMm.xMm'), { min: 1 });
        checkIntMm(out, s.yMm, f('sizeMm.yMm'), { min: 1 });
      }
      break;
    }
    case 'furniture.set': {
      if (requireField(out, p, 'action', type))
        checkEnum(out, p.action, f('action'), FURNITURE_ACTIONS);
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'furniture');
      if (p.action === 'place') {
        if (requireField(out, p, 'storeyId', type))
          checkId(out, p.storeyId, f('storeyId'), 'storey');
        if (requireField(out, p, 'catalogId', type)) checkString(out, p.catalogId, f('catalogId'));
        if (requireField(out, p, 'pt', type)) checkPt(out, p.pt, f('pt'));
      }
      if (has(p, 'pt') && p.action !== 'place') checkPt(out, p.pt, f('pt'));
      if (has(p, 'rotationDeg'))
        checkInt(out, p.rotationDeg, f('rotationDeg'), { min: -359, max: 359 });
      break;
    }
    case 'balcony.set': {
      if (requireField(out, p, 'action', type))
        checkEnum(out, p.action, f('action'), BALCONY_ACTIONS);
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'balcony');
      if (p.action === 'add') {
        if (requireField(out, p, 'storeyId', type))
          checkId(out, p.storeyId, f('storeyId'), 'storey');
        if (requireField(out, p, 'polygon', type)) checkPolygon(out, p.polygon, f('polygon'));
      } else if (has(p, 'polygon')) {
        checkPolygon(out, p.polygon, f('polygon'));
      }
      if (has(p, 'railingKind')) checkEnum(out, p.railingKind, f('railingKind'), RAILING_KINDS);
      if (has(p, 'railingHeightMm'))
        checkIntMm(out, p.railingHeightMm, f('railingHeightMm'), { min: 0 });
      if (has(p, 'projectionMm')) checkIntMm(out, p.projectionMm, f('projectionMm'), { min: 0 });
      if (has(p, 'slabThicknessMm'))
        checkIntMm(out, p.slabThicknessMm, f('slabThicknessMm'), { min: 1 });
      break;
    }
    case 'facade.apply_kit': {
      if (requireField(out, p, 'kitId', type) && p.kitId !== null) {
        checkString(out, p.kitId, f('kitId'));
      }
      if (requireField(out, p, 'seed', type)) checkInt(out, p.seed, f('seed'), { min: 0 });
      if (has(p, 'colorwayId') && p.colorwayId !== null)
        checkString(out, p.colorwayId, f('colorwayId'));
      if (requireField(out, p, 'components', type)) {
        if (!Array.isArray(p.components)) {
          out.push(
            issue('OP_FIELD_NOT_OBJECT', 'payload.components must be an array.', {
              field: f('components'),
            }),
          );
        } else {
          p.components.forEach((c, i) => {
            if (!isPlainObject(c)) {
              out.push(
                issue(
                  'OP_FIELD_NOT_OBJECT',
                  `payload.components[${String(i)}] must be an object.`,
                  {
                    field: `${f('components')}[${String(i)}]`,
                  },
                ),
              );
              return;
            }
            checkId(out, c.id, `${f('components')}[${String(i)}].id`, 'facadecomp');
            checkEnum(out, c.kind, `${f('components')}[${String(i)}].kind`, FACADE_COMPONENT_KINDS);
            if (
              has(c, 'params') &&
              checkObject(out, c.params, `${f('components')}[${String(i)}].params`)
            ) {
              checkJsonIntegral(out, c.params, `${f('components')}[${String(i)}].params`);
            }
          });
        }
      }
      break;
    }
    case 'facade.edit_component': {
      if (requireField(out, p, 'componentId', type)) {
        checkId(out, p.componentId, f('componentId'), 'facadecomp');
      }
      if (requireField(out, p, 'patch', type) && checkObject(out, p.patch, f('patch'))) {
        checkJsonIntegral(out, p.patch, f('patch'));
      }
      break;
    }
    case 'material.assign': {
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'material');
      if (requireField(out, p, 'target', type) && checkObject(out, p.target, f('target'))) {
        const t = p.target as Record<string, unknown>;
        checkEnum(out, t.group, f('target.group'), SURFACE_GROUPS);
        if (t.storeyId !== null && t.storeyId !== undefined) {
          checkId(out, t.storeyId, f('target.storeyId'), 'storey');
        }
        if (t.elementId !== null && t.elementId !== undefined && tryParseId(t.elementId) === null) {
          out.push(
            issue('OP_FIELD_BAD_ID', 'payload.target.elementId must be an element id or null.', {
              field: f('target.elementId'),
            }),
          );
        }
      }
      if (requireField(out, p, 'materialId', type) && p.materialId !== null) {
        checkString(out, p.materialId, f('materialId'));
      }
      break;
    }
    case 'levels.set': {
      const keys = ['plinthMm', 'sillDefaultMm', 'lintelDefaultMm', 'parapetMm'];
      let any = false;
      for (const k of keys) {
        if (has(p, k)) {
          any = true;
          checkIntMm(out, p[k], f(k), { min: 0, max: 6000 });
        }
      }
      if (has(p, 'fflPerStoreyMm')) {
        any = true;
        if (!Array.isArray(p.fflPerStoreyMm) || !p.fflPerStoreyMm.every((v) => isIntMm(v))) {
          out.push(
            issue('OP_FIELD_NOT_INT_MM', 'payload.fflPerStoreyMm must be integer millimetres.', {
              field: f('fflPerStoreyMm'),
            }),
          );
        }
      }
      if (!any) {
        out.push(
          issue('OP_FIELD_MISSING', 'levels.set needs at least one field to set.', {
            field: 'payload',
          }),
        );
      }
      break;
    }
    case 'solver.apply_option': {
      if (requireField(out, p, 'solverJobId', type))
        checkString(out, p.solverJobId, f('solverJobId'));
      if (requireField(out, p, 'optionIndex', type))
        checkInt(out, p.optionIndex, f('optionIndex'), { min: 0 });
      if (requireField(out, p, 'ops', type)) {
        if (!Array.isArray(p.ops)) {
          out.push(
            issue('OP_FIELD_NOT_OBJECT', 'payload.ops must be an array of ops.', {
              field: f('ops'),
            }),
          );
        } else {
          p.ops.forEach((inner, i) => {
            for (const sub of validateOpShape(inner)) {
              out.push({ ...sub, field: `${f('ops')}[${String(i)}].${sub.field ?? ''}` });
            }
          });
        }
      }
      break;
    }
    case 'annotation.set': {
      if (requireField(out, p, 'action', type))
        checkEnum(out, p.action, f('action'), ANNOTATION_ACTIONS);
      if (requireField(out, p, 'id', type)) checkId(out, p.id, f('id'), 'annotation');
      if (p.action === 'add') {
        if (requireField(out, p, 'sheetId', type)) checkId(out, p.sheetId, f('sheetId'), 'sheet');
        if (requireField(out, p, 'anchorKind', type)) {
          checkEnum(out, p.anchorKind, f('anchorKind'), ANNOTATION_ANCHOR_KINDS);
        }
      } else if (has(p, 'anchorKind')) {
        checkEnum(out, p.anchorKind, f('anchorKind'), ANNOTATION_ANCHOR_KINDS);
      }
      if (has(p, 'anchorElementId') && p.anchorElementId !== null) {
        if (tryParseId(p.anchorElementId) === null) {
          out.push(
            issue('OP_FIELD_BAD_ID', 'payload.anchorElementId must be an element id or null.', {
              field: f('anchorElementId'),
            }),
          );
        }
      }
      if (has(p, 'payload') && checkObject(out, p.payload, f('payload'))) {
        checkJsonIntegral(out, p.payload, f('payload'));
      }
      break;
    }
    default: {
      // Exhaustiveness: a new op type without a shape validator lands here.
      out.push(
        issue('OP_UNKNOWN_TYPE', `No shape validator for op type ${String(type)}.`, {
          field: 'type',
        }),
      );
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Document preconditions for an op
// ---------------------------------------------------------------------------

function missing(code: ValidationCode, kind: string, id: unknown, fix: string): ValidationIssue {
  return issue(code, `No ${kind} with id ${String(id)} in this design.`, {
    elementIds: [String(id)],
    actual: String(id),
    fix,
  });
}

/**
 * Validate that an op can be applied to THIS document: referenced elements
 * exist, new ids are free, indices are in range, openings fit their host wall.
 *
 * `fold()` calls `validateOpShape` then this; if either returns issues the op is
 * rejected with `OpRejectedError` and the document is untouched.
 *
 * PRECONDITION: `validateOpShape(op)` returned no issues. This function trusts
 * the payload's shape and only asks document questions.
 */
export function validateOpAgainstDoc(doc: ProjectDoc, op: Op): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  const h = doc.house;
  const p = op.payload as Record<string, unknown>;
  const allIds = new Set<string>([
    ...h.storeys.map((s) => s.id),
    ...h.walls.map((w) => w.id),
    ...h.openings.map((o) => o.id),
    ...h.rooms.map((r) => r.id),
    ...h.stairs.map((s) => s.id),
    ...h.slabs.map((s) => s.id),
    ...h.columns.map((c) => c.id),
    ...h.furniture.map((fi) => fi.id),
    ...h.balconies.map((b) => b.id),
    ...h.facade.components.map((c) => c.id),
    ...h.materials.map((m) => m.id),
    ...doc.annotations.map((a) => a.id),
  ]);

  const requireFreeId = (id: unknown): void => {
    if (typeof id === 'string' && allIds.has(id)) {
      out.push(
        issue('OP_ID_ALREADY_EXISTS', `Id ${id} is already used in this design.`, {
          elementIds: [id],
          fix: 'Mint a fresh id with newId().',
        }),
      );
    }
  };
  const requireStorey = (id: unknown): boolean => {
    const ok = h.storeys.some((s) => s.id === id);
    if (!ok)
      out.push(
        missing(
          'STOREY_UNKNOWN',
          'storey',
          id,
          'Add the storey first, or use an existing storeyId.',
        ),
      );
    return ok;
  };

  switch (op.type) {
    case 'plot.set_road': {
      const edges = doc.plot.boundary.length;
      const idx = op.payload.edgeIndex;
      if (edges === 0) {
        out.push(
          issue(
            'PLOT_BOUNDARY_NOT_CLOSED',
            'Set the plot boundary before assigning roads to edges.',
            {
              fix: 'Send plot.set_boundary first.',
            },
          ),
        );
      } else if (idx >= edges) {
        out.push(
          issue(
            'PLOT_EDGE_UNKNOWN',
            `The plot has ${String(edges)} edges; edge ${String(idx)} does not exist.`,
            {
              field: 'payload.edgeIndex',
              actual: idx,
              limit: edges - 1,
            },
          ),
        );
      }
      break;
    }
    case 'storey.add': {
      requireFreeId(op.payload.id);
      if (op.payload.index > h.storeys.length) {
        out.push(
          issue(
            'STOREY_INDEX_OUT_OF_RANGE',
            `Cannot insert a storey at index ${String(op.payload.index)}; there are ${String(h.storeys.length)}.`,
            { field: 'payload.index', actual: op.payload.index, limit: h.storeys.length },
          ),
        );
      }
      break;
    }
    case 'storey.remove': {
      if (op.payload.index >= h.storeys.length) {
        out.push(
          issue(
            'STOREY_INDEX_OUT_OF_RANGE',
            `There is no storey at index ${String(op.payload.index)}.`,
            { field: 'payload.index', actual: op.payload.index, limit: h.storeys.length - 1 },
          ),
        );
      }
      break;
    }
    case 'storey.set_height': {
      requireStorey(op.payload.storeyId);
      break;
    }
    case 'wall.add': {
      requireFreeId(op.payload.id);
      requireStorey(op.payload.storeyId);
      const dup = h.walls.find(
        (w) =>
          w.storeyId === op.payload.storeyId &&
          overlapsWall({ a: w.a, b: w.b }, { a: op.payload.a, b: op.payload.b }),
      );
      if (dup) {
        out.push(
          issue('WALL_DUPLICATE', 'There is already a wall along that line.', {
            elementIds: [dup.id],
            fix: 'Move or delete the existing wall instead of adding a duplicate.',
          }),
        );
      }
      break;
    }
    case 'wall.move':
    case 'wall.set_thickness':
    case 'wall.delete': {
      const wallId = (p.wallId as string) ?? '';
      const wall = h.walls.find((w) => w.id === wallId);
      if (!wall) {
        out.push(
          missing('WALL_UNKNOWN', 'wall', wallId, 'Use a wallId that exists on this storey.'),
        );
        break;
      }
      if (op.type === 'wall.move') {
        const moved = { a: op.payload.a, b: op.payload.b };
        const dup = h.walls.find(
          (w) =>
            w.id !== wall.id &&
            w.storeyId === wall.storeyId &&
            overlapsWall({ a: w.a, b: w.b }, moved),
        );
        if (dup) {
          out.push(
            issue('WALL_DUPLICATE', 'Moving the wall there would make it overlap another wall.', {
              elementIds: [wall.id, dup.id],
              fix: 'Offset the wall by at least its thickness, or delete the other wall.',
            }),
          );
        }
        const newLen = segmentLengthMm({ a: op.payload.a, b: op.payload.b });
        for (const o of h.openings.filter((x) => x.wallId === wall.id)) {
          const fit = openingFitIssue(o.id, o.offsetMm, o.widthMm, newLen);
          if (fit) out.push(fit);
        }
      }
      break;
    }
    case 'wall.split': {
      requireFreeId(op.payload.newWallId);
      const wall = h.walls.find((w) => w.id === op.payload.wallId);
      if (!wall) {
        out.push(missing('WALL_UNKNOWN', 'wall', op.payload.wallId, 'Use an existing wallId.'));
        break;
      }
      const len = segmentLengthMm({ a: wall.a, b: wall.b });
      if (op.payload.atMm <= 0 || op.payload.atMm >= len) {
        out.push(
          issue(
            'WALL_SPLIT_OUT_OF_RANGE',
            `Split point must be between 1mm and ${String(len - 1)}mm along the wall.`,
            { elementIds: [wall.id], field: 'payload.atMm', actual: op.payload.atMm, limit: len },
          ),
        );
      }
      break;
    }
    case 'opening.add': {
      requireFreeId(op.payload.id);
      const wall = h.walls.find((w) => w.id === op.payload.wallId);
      if (!wall) {
        out.push(
          missing(
            'WALL_UNKNOWN',
            'wall',
            op.payload.wallId,
            'Host the opening on an existing wall.',
          ),
        );
        break;
      }
      const len = segmentLengthMm({ a: wall.a, b: wall.b });
      const fit = openingFitIssue(op.payload.id, op.payload.offsetMm, op.payload.widthMm, len);
      if (fit) out.push(fit);
      const storey = h.storeys.find((s) => s.id === wall.storeyId);
      if (storey && op.payload.sillMm + op.payload.heightMm > storey.heightMm) {
        out.push(
          heightIssue(op.payload.id, op.payload.sillMm + op.payload.heightMm, storey.heightMm),
        );
      }
      break;
    }
    case 'opening.move':
    case 'opening.resize':
    case 'opening.flip':
    case 'opening.delete': {
      const openingId = (p.openingId as string) ?? '';
      const opening = h.openings.find((o) => o.id === openingId);
      if (!opening) {
        out.push(missing('OPENING_UNKNOWN', 'opening', openingId, 'Use an openingId that exists.'));
        break;
      }
      if (op.type === 'opening.move') {
        const targetWallId = op.payload.wallId ?? opening.wallId;
        const wall = h.walls.find((w) => w.id === targetWallId);
        if (!wall) {
          out.push(missing('WALL_UNKNOWN', 'wall', targetWallId, 'Re-host onto an existing wall.'));
          break;
        }
        const len = segmentLengthMm({ a: wall.a, b: wall.b });
        const fit = openingFitIssue(opening.id, op.payload.offsetMm, opening.widthMm, len);
        if (fit) out.push(fit);
      }
      if (op.type === 'opening.resize') {
        const wall = h.walls.find((w) => w.id === opening.wallId);
        const width = op.payload.widthMm ?? opening.widthMm;
        const height = op.payload.heightMm ?? opening.heightMm;
        const sill = op.payload.sillMm ?? opening.sillMm;
        if (wall) {
          const len = segmentLengthMm({ a: wall.a, b: wall.b });
          const fit = openingFitIssue(opening.id, opening.offsetMm, width, len);
          if (fit) out.push(fit);
          const storey = h.storeys.find((s) => s.id === wall.storeyId);
          if (storey && sill + height > storey.heightMm) {
            out.push(heightIssue(opening.id, sill + height, storey.heightMm));
          }
        }
      }
      break;
    }
    case 'room.assign':
    case 'room.set_target': {
      const roomId = (p.roomId as string) ?? '';
      if (!h.rooms.some((r) => r.id === roomId)) {
        out.push(
          missing(
            'ROOM_UNKNOWN',
            'room',
            roomId,
            'Rooms are detected from walls — enclose the space first, then assign it.',
          ),
        );
      }
      break;
    }
    case 'stair.add': {
      requireFreeId(op.payload.id);
      if (requireStorey(op.payload.storeyId)) {
        const storey = h.storeys.find((s) => s.id === op.payload.storeyId);
        if (storey) {
          const rise = op.payload.risersCount * op.payload.riserMm;
          const riseIssue = stairRiseIssue(
            op.payload.id,
            rise,
            storey.heightMm,
            op.payload.risersCount,
          );
          if (riseIssue) out.push(riseIssue);
        }
      }
      break;
    }
    case 'stair.edit':
    case 'stair.delete': {
      const stairId = (p.stairId as string) ?? '';
      const stair = h.stairs.find((s) => s.id === stairId);
      if (!stair) {
        out.push(missing('STAIR_UNKNOWN', 'stair', stairId, 'Use an existing stairId.'));
        break;
      }
      if (op.type === 'stair.edit') {
        const storey = h.storeys.find((s) => s.id === stair.storeyId);
        const risers = op.payload.patch.risersCount ?? stair.risersCount;
        const riser = op.payload.patch.riserMm ?? stair.riserMm;
        if (storey) {
          const riseIssue = stairRiseIssue(stair.id, risers * riser, storey.heightMm, risers);
          if (riseIssue) out.push(riseIssue);
        }
      }
      break;
    }
    case 'column.set': {
      if (op.payload.action === 'add') {
        requireFreeId(op.payload.id);
        requireStorey(op.payload.storeyId);
      } else if (!h.columns.some((c) => c.id === op.payload.id)) {
        out.push(
          missing(
            'COLUMN_UNKNOWN',
            'column',
            op.payload.id,
            'Add the column before moving or deleting it.',
          ),
        );
      }
      break;
    }
    case 'furniture.set': {
      if (op.payload.action === 'place') {
        requireFreeId(op.payload.id);
        requireStorey(op.payload.storeyId);
      } else if (!h.furniture.some((fi) => fi.id === op.payload.id)) {
        out.push(
          missing(
            'FURNITURE_UNKNOWN',
            'furniture item',
            op.payload.id,
            'Place the item before transforming it.',
          ),
        );
      }
      break;
    }
    case 'balcony.set': {
      if (op.payload.action === 'add') {
        requireFreeId(op.payload.id);
        requireStorey(op.payload.storeyId);
      } else if (!h.balconies.some((b) => b.id === op.payload.id)) {
        out.push(
          missing(
            'BALCONY_UNKNOWN',
            'balcony',
            op.payload.id,
            'Add the balcony before editing it.',
          ),
        );
      }
      break;
    }
    case 'facade.edit_component': {
      if (!h.facade.components.some((c) => c.id === op.payload.componentId)) {
        out.push(
          missing(
            'FACADE_COMPONENT_UNKNOWN',
            'facade component',
            op.payload.componentId,
            'Apply a facade kit first.',
          ),
        );
      }
      break;
    }
    case 'material.assign': {
      const existing = h.materials.some((m) => m.id === op.payload.id);
      if (!existing && op.payload.materialId === null) {
        out.push(
          missing(
            'MATERIAL_ASSIGNMENT_UNKNOWN',
            'material assignment',
            op.payload.id,
            'Nothing to clear — this assignment does not exist.',
          ),
        );
      }
      // `!= null` (loose), matching the shape check at the top of this file: for a
      // wire payload carrying `target: { group }` only, `storeyId` is `undefined`,
      // and `undefined !== null` is true — so the strict form called
      // requireStorey(undefined) and emitted a spurious STOREY_UNKNOWN. The Python
      // mirror treats absent as null and skips, so the same op was accepted by the
      // server and rejected in the browser: an optimistic dispatch rolled back an
      // edit the server would have taken.
      if (op.payload.target.storeyId != null) requireStorey(op.payload.target.storeyId);
      break;
    }
    case 'levels.set': {
      if (op.payload.fflPerStoreyMm && op.payload.fflPerStoreyMm.length !== h.storeys.length) {
        out.push(
          issue(
            'LEVELS_INVALID',
            `fflPerStoreyMm has ${String(op.payload.fflPerStoreyMm.length)} entries but there are ${String(h.storeys.length)} storeys.`,
            {
              field: 'payload.fflPerStoreyMm',
              actual: op.payload.fflPerStoreyMm.length,
              limit: h.storeys.length,
            },
          ),
        );
      }
      break;
    }
    case 'annotation.set': {
      if (op.payload.action === 'add') {
        requireFreeId(op.payload.id);
      } else if (!doc.annotations.some((a) => a.id === op.payload.id)) {
        out.push(
          missing('ANNOTATION_UNKNOWN', 'annotation', op.payload.id, 'Add the annotation first.'),
        );
      }
      break;
    }
    case 'solver.apply_option': {
      // Each inner op is validated as it is folded (fold applies the group
      // transactionally), so nothing to pre-check here beyond the shape.
      break;
    }
    default:
      break;
  }
  return out;
}

function overlapsWall(a: { a: Pt; b: Pt }, b: { a: Pt; b: Pt }): boolean {
  const ov = collinearOverlap(a, b);
  if (!ov) return false;
  // only an overlap of non-zero length counts as "exactly overlapping"
  if (ptEq(ov.a, ov.b)) return false;
  // and only when they are actually collinear (collinearOverlap projects, so
  // confirm both endpoints of b lie on a's infinite line)
  const cross1 = (a.b.x - a.a.x) * (b.a.y - a.a.y) - (a.b.y - a.a.y) * (b.a.x - a.a.x);
  const cross2 = (a.b.x - a.a.x) * (b.b.y - a.a.y) - (a.b.y - a.a.y) * (b.b.x - a.a.x);
  return cross1 === 0 && cross2 === 0;
}

function openingFitIssue(
  openingId: string,
  offsetMm: number,
  widthMm: number,
  wallLengthMm: number,
): ValidationIssue | null {
  const usable = wallLengthMm - 2 * WALL_END_MARGIN_MM;
  if (widthMm > usable) {
    return issue(
      'OPENING_OUT_OF_WALL',
      `This opening is ${String(widthMm)}mm wide but the wall only offers ${String(Math.max(0, usable))}mm between the ${String(WALL_END_MARGIN_MM)}mm end margins.`,
      {
        elementIds: [openingId],
        field: 'payload.widthMm',
        actual: widthMm,
        limit: Math.max(0, usable),
        fix: `Narrow the opening to ${String(Math.max(0, usable))}mm or host it on a longer wall.`,
      },
    );
  }
  // Complementary floor/ceil halves: start+end must span exactly widthMm, which a
  // half-away-from-zero helper would break. Integer input, so both are exact.
  // eslint-disable-next-line no-restricted-properties -- see above
  const start = offsetMm - Math.floor(widthMm / 2);
  // eslint-disable-next-line no-restricted-properties -- see above
  const end = offsetMm + Math.ceil(widthMm / 2);
  if (start < WALL_END_MARGIN_MM || end > wallLengthMm - WALL_END_MARGIN_MM) {
    // eslint-disable-next-line no-restricted-properties -- same exact-halving pair as above
    const minOffset = WALL_END_MARGIN_MM + Math.floor(widthMm / 2);
    // eslint-disable-next-line no-restricted-properties -- same exact-halving pair as above
    const maxOffset = wallLengthMm - WALL_END_MARGIN_MM - Math.ceil(widthMm / 2);
    return issue(
      'OPENING_OUT_OF_WALL',
      `The opening must sit between ${String(minOffset)}mm and ${String(maxOffset)}mm along the wall to keep ${String(WALL_END_MARGIN_MM)}mm at each end.`,
      {
        elementIds: [openingId],
        field: 'payload.offsetMm',
        actual: offsetMm,
        limit: `${String(minOffset)}..${String(maxOffset)}`,
        fix: `Set offsetMm between ${String(minOffset)} and ${String(maxOffset)}.`,
      },
    );
  }
  return null;
}

function heightIssue(openingId: string, topMm: number, storeyHeightMm: number): ValidationIssue {
  return issue(
    'OPENING_EXCEEDS_STOREY_HEIGHT',
    `Sill + height is ${String(topMm)}mm, taller than the ${String(storeyHeightMm)}mm storey.`,
    {
      elementIds: [openingId],
      field: 'payload.heightMm',
      actual: topMm,
      limit: storeyHeightMm,
      fix: `Reduce the height or sill so they total at most ${String(storeyHeightMm)}mm.`,
    },
  );
}

function stairRiseIssue(
  stairId: string,
  totalRiseMm: number,
  storeyHeightMm: number,
  risersCount: number,
): ValidationIssue | null {
  const delta = Math.abs(totalRiseMm - storeyHeightMm);
  if (delta <= STAIR_RISE_TOLERANCE_MM) return null;
  const suggested = roundHalfAwayFromZero(storeyHeightMm / Math.max(1, risersCount));
  return issue(
    'STAIR_RISE_MISMATCH',
    `${String(risersCount)} risers total ${String(totalRiseMm)}mm but the storey is ${String(storeyHeightMm)}mm (±${String(STAIR_RISE_TOLERANCE_MM)}mm allowed).`,
    {
      elementIds: [stairId],
      field: 'payload.riserMm',
      actual: totalRiseMm,
      limit: storeyHeightMm,
      fix: `Use riserMm ${String(suggested)} with ${String(risersCount)} risers, or change risersCount.`,
    },
  );
}

// ---------------------------------------------------------------------------
// Whole-document invariants
// ---------------------------------------------------------------------------

export interface ValidateModelOptions {
  /** Limit the (quadratic) wall-overlap check to these storeys. */
  readonly storeyIds?: readonly string[];
  /** Include warnings (default true). */
  readonly includeWarnings?: boolean;
}

/**
 * The §3 fold invariants over a whole document. `fold()` runs this on the
 * candidate next state (scoped to the touched storeys) and refuses to return a
 * document that breaks it.
 */
export function validateModel(doc: ProjectDoc, opts: ValidateModelOptions = {}): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  const h = doc.house;
  const scope = opts.storeyIds ? new Set(opts.storeyIds) : null;
  const inScope = (storeyId: string): boolean => scope === null || scope.has(storeyId);

  if (h.schemaVersion !== doc.schemaVersion) {
    out.push(
      issue(
        'SCHEMA_VERSION_UNSUPPORTED',
        `House schemaVersion ${String(h.schemaVersion)} does not match document ${String(doc.schemaVersion)}.`,
      ),
    );
  }

  // --- duplicate ids across the whole document
  const seen = new Map<string, number>();
  const bump = (id: string): void => {
    seen.set(id, (seen.get(id) ?? 0) + 1);
  };
  h.storeys.forEach((s) => bump(s.id));
  h.walls.forEach((w) => bump(w.id));
  h.openings.forEach((o) => bump(o.id));
  h.rooms.forEach((r) => bump(r.id));
  h.stairs.forEach((s) => bump(s.id));
  h.slabs.forEach((s) => bump(s.id));
  h.columns.forEach((c) => bump(c.id));
  h.furniture.forEach((fi) => bump(fi.id));
  h.balconies.forEach((b) => bump(b.id));
  h.facade.components.forEach((c) => bump(c.id));
  h.materials.forEach((m) => bump(m.id));
  doc.annotations.forEach((a) => bump(a.id));
  for (const [id, count] of seen) {
    if (count > 1) {
      out.push(
        issue('DUPLICATE_ELEMENT_ID', `Id ${id} appears ${String(count)} times.`, {
          elementIds: [id],
          actual: count,
          limit: 1,
        }),
      );
    }
  }

  // --- plot
  if (doc.plot.boundary.length > 0 && !polygonIsClosedRing(doc.plot.boundary)) {
    out.push(
      issue(
        'PLOT_BOUNDARY_NOT_CLOSED',
        'The plot boundary must be a closed ring with non-zero area.',
        {
          fix: 'Fix the boundary vertices so the outline closes without crossing itself.',
        },
      ),
    );
  }
  if (
    !Number.isSafeInteger(doc.plot.northDeg) ||
    doc.plot.northDeg < 0 ||
    doc.plot.northDeg > 359
  ) {
    out.push(
      issue('PLOT_NORTH_INVALID', 'North must be an integer 0–359 degrees.', {
        actual: doc.plot.northDeg,
        limit: '0..359',
      }),
    );
  }

  // --- storeys & levels
  for (const s of h.storeys) {
    if (!isIntMm(s.heightMm) || s.heightMm <= 0) {
      out.push(
        issue('STOREY_HEIGHT_INVALID', `Storey ${s.name || s.id} needs a positive height in mm.`, {
          elementIds: [s.id],
          actual: s.heightMm,
        }),
      );
    }
  }
  if (h.levels.fflPerStoreyMm.length !== h.storeys.length) {
    out.push(
      issue(
        'LEVELS_INVALID',
        `levels.fflPerStoreyMm has ${String(h.levels.fflPerStoreyMm.length)} entries for ${String(h.storeys.length)} storeys.`,
        { actual: h.levels.fflPerStoreyMm.length, limit: h.storeys.length },
      ),
    );
  }

  // --- walls
  const storeyById = new Map(h.storeys.map((s) => [s.id, s]));
  const wallsInScope = h.walls.filter((w) => inScope(w.storeyId));
  for (const w of wallsInScope) {
    if (ptEq(w.a, w.b)) {
      out.push(
        issue('WALL_ZERO_LENGTH', 'A wall has zero length.', {
          elementIds: [w.id],
          fix: 'Delete the wall or give it two different endpoints.',
        }),
      );
    }
    if (!isIntMm(w.thicknessMm) || w.thicknessMm <= 0 || w.thicknessMm > MAX_WALL_THICKNESS_MM) {
      out.push(
        issue(
          'WALL_THICKNESS_INVALID',
          `Wall thickness ${String(w.thicknessMm)}mm is out of range.`,
          {
            elementIds: [w.id],
            actual: w.thicknessMm,
            limit: `1..${String(MAX_WALL_THICKNESS_MM)}`,
          },
        ),
      );
    }
    if (!storeyById.has(w.storeyId)) {
      out.push(
        missing(
          'STOREY_UNKNOWN',
          'storey',
          w.storeyId,
          'Re-parent the wall to an existing storey.',
        ),
      );
    }
  }
  // "no two walls exactly overlapping" — quadratic, so scope it per storey
  const byStorey = new Map<string, typeof wallsInScope>();
  for (const w of wallsInScope) {
    const list = byStorey.get(w.storeyId);
    if (list) list.push(w);
    else byStorey.set(w.storeyId, [w]);
  }
  for (const list of byStorey.values()) {
    for (let i = 0; i < list.length; i++) {
      const wi = list[i]!;
      for (let j = i + 1; j < list.length; j++) {
        const wj = list[j]!;
        if (overlapsWall({ a: wi.a, b: wi.b }, { a: wj.a, b: wj.b })) {
          out.push(
            issue('WALL_DUPLICATE', 'Two walls lie on top of each other.', {
              elementIds: [wi.id, wj.id],
              fix: 'Delete one of them, or offset it by at least its thickness.',
            }),
          );
        }
      }
    }
  }

  // --- openings
  const wallById = new Map(h.walls.map((w) => [w.id, w]));
  for (const o of h.openings) {
    const wall = wallById.get(o.wallId);
    if (!wall) {
      out.push(missing('OPENING_UNKNOWN', 'host wall', o.wallId, 'Re-host or delete the opening.'));
      continue;
    }
    if (!inScope(wall.storeyId)) continue;
    if (o.widthMm <= 0 || o.heightMm <= 0) {
      out.push(
        issue('OPENING_DIMENSION_INVALID', 'An opening must have positive width and height.', {
          elementIds: [o.id],
          actual: `${String(o.widthMm)}×${String(o.heightMm)}`,
        }),
      );
    }
    if (o.sillMm < 0) {
      out.push(
        issue('OPENING_SILL_INVALID', 'Sill height cannot be negative.', {
          elementIds: [o.id],
          actual: o.sillMm,
        }),
      );
    }
    const len = segmentLengthMm({ a: wall.a, b: wall.b });
    const fit = openingFitIssue(o.id, o.offsetMm, o.widthMm, len);
    if (fit) out.push(fit);
    const storey = storeyById.get(wall.storeyId);
    if (storey && o.sillMm + o.heightMm > storey.heightMm) {
      out.push(heightIssue(o.id, o.sillMm + o.heightMm, storey.heightMm));
    }
  }

  // --- stairs
  for (const s of h.stairs) {
    if (!inScope(s.storeyId)) continue;
    const storey = storeyById.get(s.storeyId);
    if (!storey) {
      out.push(missing('STOREY_UNKNOWN', 'storey', s.storeyId, 'Re-parent the stair.'));
      continue;
    }
    if (s.riserMm <= 0 || s.treadMm <= 0 || s.widthMm <= 0 || s.risersCount <= 1) {
      out.push(
        issue(
          'STAIR_DIMENSION_INVALID',
          'Stair riser, tread, width and riser count must all be positive.',
          {
            elementIds: [s.id],
          },
        ),
      );
      continue;
    }
    const riseIssue = stairRiseIssue(
      s.id,
      s.risersCount * s.riserMm,
      storey.heightMm,
      s.risersCount,
    );
    if (riseIssue) out.push(riseIssue);
  }

  // --- rooms closed
  for (const r of h.rooms) {
    if (!inScope(r.storeyId)) continue;
    if (!polygonIsClosedRing(r.polygon)) {
      out.push(
        issue('ROOM_NOT_CLOSED', `Room ${r.name || r.id} is not a closed area.`, {
          elementIds: [r.id],
          fix: 'Close the surrounding walls; rooms are detected from enclosed space.',
        }),
      );
      continue;
    }
    const area = polygonAreaMm2(r.polygon);
    if (area !== r.areaMm2) {
      out.push(
        issue('ROOM_NOT_CLOSED', `Room ${r.name || r.id} has a stale area.`, {
          elementIds: [r.id],
          actual: r.areaMm2,
          limit: area,
          severity: 'warning',
          fix: 'Re-run room detection (fold recomputes it).',
        }),
      );
    }
  }

  // --- balconies
  for (const b of h.balconies) {
    if (!inScope(b.storeyId)) continue;
    if (!polygonIsClosedRing(b.polygon)) {
      out.push(
        issue('BALCONY_POLYGON_INVALID', 'A balcony outline must be a closed ring.', {
          elementIds: [b.id],
        }),
      );
    }
  }

  // --- columns
  for (const c of h.columns) {
    if (!inScope(c.storeyId)) continue;
    if (c.sizeMm.xMm <= 0 || c.sizeMm.yMm <= 0) {
      out.push(
        issue('COLUMN_SIZE_INVALID', 'Column size must be positive.', { elementIds: [c.id] }),
      );
    }
  }

  return opts.includeWarnings === false ? out.filter((i) => i.severity === 'error') : out;
}

/** True when nothing in `issues` is an error. */
export function isAcceptable(issues: readonly ValidationIssue[]): boolean {
  return !issues.some((i) => i.severity === 'error');
}

/** Group issues by code — handy for the compliance strip and copilot feedback. */
export function issuesByCode(
  issues: readonly ValidationIssue[],
): ReadonlyMap<ValidationCode, ValidationIssue[]> {
  const map = new Map<ValidationCode, ValidationIssue[]>();
  for (const i of issues) {
    const list = map.get(i.code);
    if (list) list.push(i);
    else map.set(i.code, [i]);
  }
  return map;
}

/**
 * Compact, LLM-friendly rendering of rejection reasons (§10 self-correction).
 * One line per issue: `CODE field=… actual=… limit=… message (fix)`.
 */
export function renderIssuesForLlm(issues: readonly ValidationIssue[]): string {
  return issues
    .map((i) => {
      const parts: string[] = [i.code];
      if (i.field) parts.push(`field=${i.field}`);
      if (i.actual !== undefined && i.actual !== null) parts.push(`actual=${String(i.actual)}`);
      if (i.limit !== undefined && i.limit !== null) parts.push(`limit=${String(i.limit)}`);
      parts.push(`— ${i.message}`);
      if (i.fix) parts.push(`FIX: ${i.fix}`);
      return parts.join(' ');
    })
    .join('\n');
}

/** All model-invariant codes (as opposed to op-shape codes) — used by the UI copy map. */
export const MODEL_INVARIANT_CODES: readonly ValidationCode[] = [
  'WALL_ZERO_LENGTH',
  'WALL_THICKNESS_INVALID',
  'WALL_DUPLICATE',
  'OPENING_DIMENSION_INVALID',
  'OPENING_OUT_OF_WALL',
  'OPENING_EXCEEDS_STOREY_HEIGHT',
  'OPENING_SILL_INVALID',
  'STAIR_RISE_MISMATCH',
  'STAIR_DIMENSION_INVALID',
  'ROOM_NOT_CLOSED',
  'STOREY_HEIGHT_INVALID',
  'PLOT_BOUNDARY_NOT_CLOSED',
  'PLOT_NORTH_INVALID',
  'LEVELS_INVALID',
  'BALCONY_POLYGON_INVALID',
  'COLUMN_SIZE_INVALID',
  'DUPLICATE_ELEMENT_ID',
];

/** Convenience: throw unless the document satisfies the §3 invariants. */
export function assertValidModel(doc: ProjectDoc): void {
  const issues = validateModel(doc, { includeWarnings: false });
  if (issues.length > 0) throw new OpRejectedError('model', issues);
}
