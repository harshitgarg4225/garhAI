/**
 * ids.ts — element identity.
 *
 * Every element id is `${type}_${ulid}` — e.g. `wall_01J9Z8QK7X3B2M4N5P6R7S8T9V`.
 * The prefix makes logs, op payloads and LLM prompts self-describing (a copilot
 * that sees `room_...` in a payload slot typed `wallId` is obviously wrong), and
 * the ULID makes ids sortable by creation time and collision-free without a
 * central sequence.
 *
 * TWO KINDS OF ID:
 *  - `newId(type)`     — random ULID. Used for elements a HUMAN or the SOLVER
 *                        creates. MUST be called by the op *producer*, never
 *                        inside `fold()`: creation ops carry their id in the
 *                        payload so that `replay(ops)` is deterministic.
 *  - `derivedId(t, k)` — deterministic id from a key string, for elements the
 *                        model DERIVES (rooms from planar subdivision, slabs
 *                        per storey). Derived elements must get the same id on
 *                        every replay of the same op log, so a random ULID is
 *                        not allowed here.
 */

import { ulid as ulidImpl } from 'ulid';

import { sha256Utf8 } from './sha256';

/** Crockford base32 — ULID's alphabet (no I, L, O, U). */
export const CROCKFORD32 = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/** Every element family that owns an id namespace. */
export const ELEMENT_TYPES = [
  'storey',
  'wall',
  'opening',
  'room',
  'stair',
  'slab',
  'column',
  'furniture',
  'balcony',
  'facade',
  'facadecomp',
  'material',
  'annotation',
  'sheet',
  'group',
  'op',
  'plot',
  'brief',
  'version',
  'job',
] as const;

export type ElementType = (typeof ELEMENT_TYPES)[number];

const ELEMENT_TYPE_SET: ReadonlySet<string> = new Set<string>(ELEMENT_TYPES);

/**
 * A branded-ish id type. The brand is OPTIONAL, so a plain `string` is freely
 * assignable both ways: this documents intent at every call site without
 * forcing casts on the four other packages that consume these types.
 */
export type Id<T extends ElementType> = string & { readonly __idType?: T };

export type StoreyId = Id<'storey'>;
export type WallId = Id<'wall'>;
export type OpeningId = Id<'opening'>;
export type RoomId = Id<'room'>;
export type StairId = Id<'stair'>;
export type SlabId = Id<'slab'>;
export type ColumnId = Id<'column'>;
export type FurnitureId = Id<'furniture'>;
export type BalconyId = Id<'balcony'>;
export type FacadeComponentId = Id<'facadecomp'>;
export type MaterialAssignmentId = Id<'material'>;
export type AnnotationId = Id<'annotation'>;
export type SheetId = Id<'sheet'>;
export type GroupId = Id<'group'>;
export type ElementId = string;

/** `type_ULID` — 26 Crockford base32 chars, uppercase. */
export const ID_PATTERN = /^([a-z][a-z0-9]{1,15})_([0-9ABCDEFGHJKMNPQRSTVWXYZ]{26})$/;

/** Max ULID timestamp char: the first char of a valid ULID is 0-7. */
const ULID_FIRST_CHARS = '01234567';

/** Thrown when an id is malformed. */
export class IdError extends Error {
  readonly code = 'ID_INVALID';
  constructor(message: string) {
    super(message);
    this.name = 'IdError';
  }
}

// ---------------------------------------------------------------------------
// Random ids
// ---------------------------------------------------------------------------

/**
 * The ULID factory. Injectable so tests can make id generation deterministic
 * without monkey-patching the module (`setUlidFactory(seededUlid)`).
 */
export type UlidFactory = () => string;

let ulidFactory: UlidFactory | null = null;

/** Install a ULID factory (tests, or a seeded solver run). */
export function setUlidFactory(factory: UlidFactory | null): void {
  ulidFactory = factory;
}

/**
 * Deterministic, monotonic ULID factory for tests and golden fixtures.
 * `seed` fixes the 80 random bits; the 48-bit timestamp is a counter.
 */
export function seededUlidFactory(seed = 1): UlidFactory {
  let counter = 0;
  return () => {
    counter += 1;
    const time = encodeCrockford(counter, 10);
    const rand = encodeCrockford(seed * 0x9e3779b1 + counter * 0x85ebca6b, 16);
    return time + rand;
  };
}

function encodeCrockford(value: number, length: number): string {
  let n = Math.abs(Math.floor(value));
  let out = '';
  for (let i = 0; i < length; i++) {
    out = CROCKFORD32[n % 32] + out;
    n = Math.floor(n / 32);
  }
  return out;
}

/**
 * Fresh id for a NEW element. Uses the `ulid` package (MIT) unless a factory is
 * installed. Never call this inside `fold()` — see the module docstring.
 */
export function newId<T extends ElementType>(type: T): Id<T> {
  const ulidValue = ulidFactory ? ulidFactory() : defaultUlid();
  return `${type}_${ulidValue}`;
}

function defaultUlid(): string {
  return ulidImpl();
}

// ---------------------------------------------------------------------------
// Derived (deterministic) ids
// ---------------------------------------------------------------------------

/**
 * Deterministic id from a key string: `type_<130 bits of sha256(key)>`.
 *
 * The top two bits are cleared so the first character is 0-7, keeping the value
 * a *syntactically valid* ULID (48-bit timestamp range) — strict ULID parsers
 * in other tools will not choke on it.
 *
 * CROSS-LANGUAGE CONTRACT: the Python mirror must produce byte-identical ids,
 * because room ids appear in the state hash. Algorithm, exactly:
 *   1. digest = sha256(utf8(key))
 *   2. take the FIRST 130 bits of the digest, most-significant bit first
 *   3. set bit 0 and bit 1 to zero (forces the leading base32 char to 0-7)
 *   4. emit 26 characters, 5 bits each, MSB-first, through CROCKFORD32
 * Python:
 *   bits = [(digest[i // 8] >> (7 - i % 8)) & 1 for i in range(130)]
 */
export function derivedId<T extends ElementType>(type: T, key: string): Id<T> {
  const hex = sha256Utf8(key);
  // 26 chars * 5 bits = 130 bits. Take 130 bits from the digest (bytes 0..16).
  let bits: number[] = [];
  for (let i = 0; i < 17; i++) {
    const byte = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    for (let b = 7; b >= 0; b--) bits.push((byte >> b) & 1);
  }
  bits = bits.slice(0, 130);
  // force the first 5-bit group into 0..7 so the id is a legal ULID
  bits[0] = 0;
  bits[1] = 0;
  let out = '';
  for (let i = 0; i < 26; i++) {
    let v = 0;
    for (let b = 0; b < 5; b++) v = v * 2 + bits[i * 5 + b];
    out += CROCKFORD32[v];
  }
  return `${type}_${out}`;
}

/**
 * Derived id with collision escape hatch. `taken` lets the caller keep ids
 * unique when two derived elements hash to the same key (only reachable if two
 * elements really are geometrically identical).
 */
export function derivedIdUnique<T extends ElementType>(
  type: T,
  key: string,
  taken: ReadonlySet<string>,
): Id<T> {
  let candidate = derivedId(type, key);
  let salt = 0;
  while (taken.has(candidate)) {
    salt += 1;
    candidate = derivedId(type, `${key}#${salt}`);
  }
  return candidate;
}

// ---------------------------------------------------------------------------
// Parse / validate / guards
// ---------------------------------------------------------------------------

export interface ParsedId {
  readonly type: ElementType;
  readonly ulid: string;
  readonly raw: string;
}

/** Parse an id, or `null` if it is not well-formed / not a known type. */
export function tryParseId(value: unknown): ParsedId | null {
  if (typeof value !== 'string') return null;
  const m = ID_PATTERN.exec(value);
  if (!m) return null;
  const type = m[1];
  const ulidPart = m[2];
  if (!ELEMENT_TYPE_SET.has(type)) return null;
  if (!ULID_FIRST_CHARS.includes(ulidPart[0])) return null;
  return { type: type as ElementType, ulid: ulidPart, raw: value };
}

/** Parse an id or throw {@link IdError}. */
export function parseId(value: unknown): ParsedId {
  const parsed = tryParseId(value);
  if (!parsed) throw new IdError(`Not a Garh element id: ${JSON.stringify(value)}`);
  return parsed;
}

/** Type guard: any valid element id. */
export function isId(value: unknown): value is ElementId {
  return tryParseId(value) !== null;
}

/** Type guard: a valid element id of exactly this type. */
export function isIdOf<T extends ElementType>(type: T, value: unknown): value is Id<T> {
  const parsed = tryParseId(value);
  return parsed !== null && parsed.type === type;
}

/** `idType('wall_01J...') === 'wall'`, or `null`. */
export function idType(value: unknown): ElementType | null {
  return tryParseId(value)?.type ?? null;
}

/** Assert an id of a given type, returning it narrowed. */
export function assertIdOf<T extends ElementType>(type: T, value: unknown, field: string): Id<T> {
  if (!isIdOf(type, value)) {
    throw new IdError(`${field} must be a ${type} id (${type}_<ulid>), got ${JSON.stringify(value)}`);
  }
  return value;
}

/**
 * Sort ids stably: by type, then by ULID (which is lexicographically
 * time-ordered). Used wherever the model needs a canonical element order.
 */
export function compareIds(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}
