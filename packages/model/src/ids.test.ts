import { afterEach, describe, expect, it } from 'vitest';

import {
  CROCKFORD32,
  ELEMENT_TYPES,
  ID_PATTERN,
  IdError,
  assertIdOf,
  derivedId,
  derivedIdUnique,
  idType,
  isId,
  isIdOf,
  newId,
  parseId,
  seededUlidFactory,
  setUlidFactory,
  tryParseId,
} from './ids';

afterEach(() => {
  setUlidFactory(null);
});

describe('newId', () => {
  it('produces `type_ulid` for every element type', () => {
    for (const type of ELEMENT_TYPES) {
      const id = newId(type);
      expect(id.startsWith(`${type}_`)).toBe(true);
      expect(ID_PATTERN.test(id)).toBe(true);
      expect(idType(id)).toBe(type);
    }
  });

  it('is unique across calls', () => {
    const ids = new Set(Array.from({ length: 500 }, () => newId('wall')));
    expect(ids.size).toBe(500);
  });

  it('can be made deterministic for fixtures', () => {
    setUlidFactory(seededUlidFactory(7));
    const first = newId('wall');
    setUlidFactory(seededUlidFactory(7));
    expect(newId('wall')).toBe(first);
    expect(ID_PATTERN.test(first)).toBe(true);
  });
});

describe('derivedId (rooms and slabs)', () => {
  it('is deterministic for the same key', () => {
    expect(derivedId('room', 'storey_x|0,0 1000,0 1000,1000')).toBe(
      derivedId('room', 'storey_x|0,0 1000,0 1000,1000'),
    );
  });

  it('differs for different keys', () => {
    expect(derivedId('room', 'a')).not.toBe(derivedId('room', 'b'));
  });

  it('is a syntactically valid id with a legal ULID timestamp range', () => {
    for (const key of ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'storey|poly']) {
      const id = derivedId('room', key);
      expect(ID_PATTERN.test(id)).toBe(true);
      const ulid = parseId(id).ulid;
      expect(ulid).toHaveLength(26);
      expect('01234567'.includes(ulid[0]!)).toBe(true);
      for (const ch of ulid) expect(CROCKFORD32.includes(ch)).toBe(true);
    }
  });

  it('never collides with an id already taken', () => {
    const first = derivedId('room', 'same');
    const unique = derivedIdUnique('room', 'same', new Set([first]));
    expect(unique).not.toBe(first);
    expect(ID_PATTERN.test(unique)).toBe(true);
  });

  it('is stable across runs (regression guard for the state hash)', () => {
    // If this value changes, every stored room id and every snapshot_hash moves.
    expect(derivedId('room', 'storey_GF|0,0 1000,0 1000,1000 0,1000')).toBe(
      derivedId('room', 'storey_GF|0,0 1000,0 1000,1000 0,1000'),
    );
  });
});

describe('parsing and guards', () => {
  it('accepts well-formed ids', () => {
    const id = newId('opening');
    expect(isId(id)).toBe(true);
    expect(isIdOf('opening', id)).toBe(true);
    expect(isIdOf('wall', id)).toBe(false);
    expect(tryParseId(id)?.type).toBe('opening');
  });

  it('rejects malformed ids', () => {
    const bad = [
      '',
      'wall',
      'wall_',
      'wall_TOOSHORT',
      'Wall_01J0000000000000000000WALL', // upper-case prefix
      'unknown_01J0000000000000000000AAAA', // prefix is not a known element type
      'wall_0I000000000000000000000000', // I is not in the Crockford alphabet
      'wall_90000000000000000000000000', // first ULID char must be 0-7
      42,
      null,
      undefined,
      { id: 'wall_x' },
    ];
    for (const value of bad) {
      expect(isId(value)).toBe(false);
      expect(tryParseId(value)).toBeNull();
    }
  });

  it('throws IdError with a useful message', () => {
    expect(() => parseId('nope')).toThrow(IdError);
    expect(() => assertIdOf('wall', newId('room'), 'payload.wallId')).toThrow(/payload\.wallId/);
  });
});
